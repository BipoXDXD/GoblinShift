"""Contas do administrador e chaves de API no banco (docs/GoblinShiftPlan.md, 3.6 e fatia 3).

Transaction scripts com a Session direto (3.2: identidade não tem agregado). Cada função
roda dentro da transação de quem a chama, que decide o commit.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import Select, Text, bindparam, exists, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

from goblinshift.identity import sessions
from goblinshift.identity.keys import (
    DEFAULT_KEY_DAYS,
    generate_key,
    key_is_active,
    key_lifetime,
    parse_key,
    secret_matches,
)
from goblinshift.identity.principal import InvalidScopesError, Principal, Scope
from goblinshift.identity.tables import api_key, app_user, web_session

logger = logging.getLogger(__name__)

# Emissor das contas criadas pela CLI; o login por OIDC/OAuth da fatia 6 usa o do provedor.
ADMIN_ISSUER = "urn:goblinshift:admin"
ACTIVE_STATUS = "active"
BLOCKED_STATUS = "blocked"
MAX_DISPLAY_NAME_CHARS = 200
# `last_used_at` serve à tela de Acesso, não à auditoria: gravar a cada requisição seria
# uma escrita por leitura sem ganho para quem olha a data.
LAST_USED_RESOLUTION = timedelta(minutes=1)


@dataclass(frozen=True)
class Account:
    id: UUID
    display_name: str


@dataclass(frozen=True)
class IssuedKey:
    """Chave emitida. `token` só existe aqui, para ser exibido uma vez."""

    id: UUID
    public_id: str
    token: str = field(repr=False)
    expires_at: datetime


class UnknownAccountError(LookupError):
    pass


class InvalidDisplayNameError(ValueError):
    pass


def create_admin_user(session: Session, display_name: str) -> UUID:
    name = display_name.strip()
    if not 1 <= len(name) <= MAX_DISPLAY_NAME_CHARS:
        raise InvalidDisplayNameError(f"o nome deve ter de 1 a {MAX_DISPLAY_NAME_CHARS} caracteres")
    # Sem identidade externa, o sujeito é um identificador gerado e nunca reaproveitado.
    statement = (
        insert(app_user)
        .values(issuer=ADMIN_ISSUER, subject=str(uuid7()), display_name=name)
        .returning(app_user.c.id)
    )
    user_id: UUID = session.execute(statement).scalar_one()
    return user_id


def issue_key(
    session: Session,
    *,
    owner_id: UUID,
    scopes: frozenset[Scope],
    now: datetime,
    days: int = DEFAULT_KEY_DAYS,
) -> IssuedKey:
    if not scopes:
        raise InvalidScopesError("informe pelo menos um escopo")
    expires_at = now + key_lifetime(days)
    key = generate_key()
    # INSERT ... SELECT da conta: dono inexistente vira zero linhas, sem checar antes e
    # sem depender da mensagem de erro da foreign key.
    source = select(
        app_user.c.id,
        bindparam("public_id", key.public_id),
        bindparam("secret_hash", key.secret_hash),
        bindparam("scopes", sorted(scopes), type_=ARRAY(Text)),
        bindparam("created_at", now),
        bindparam("expires_at", expires_at),
    ).where(app_user.c.id == owner_id)
    statement = (
        insert(api_key)
        .from_select(
            ["owner_id", "public_id", "secret_hash", "scopes", "created_at", "expires_at"],
            source,
        )
        .returning(api_key.c.id)
    )
    key_id = session.execute(statement).scalar_one_or_none()
    if key_id is None:
        raise UnknownAccountError(f"conta {owner_id} não existe")
    return IssuedKey(id=key_id, public_id=key.public_id, token=key.token, expires_at=expires_at)


def revoke_key(session: Session, public_id: str, now: datetime) -> bool:
    """Revoga a chave; devolve False só se ela não existir.

    Revogar de novo é sucesso e preserva o instante da primeira revogação.
    """
    statement = (
        update(api_key)
        .where(api_key.c.public_id == public_id)
        .values(revoked_at=func.coalesce(api_key.c.revoked_at, now))
        .returning(api_key.c.id)
    )
    return session.execute(statement).first() is not None


def authenticate_api_key(session: Session, token: str, now: datetime) -> Principal | None:
    """Devolve o Principal da chave, ou None se ela for malformada, desconhecida ou inválida.

    Registrar o uso faz parte de autenticar: é o que alimenta o "último uso" da chave.
    """
    presented = parse_key(token)
    if presented is None:
        return None
    row = session.execute(
        select(
            api_key.c.id,
            api_key.c.owner_id,
            api_key.c.secret_hash,
            api_key.c.scopes,
            api_key.c.expires_at,
            api_key.c.revoked_at,
            app_user.c.status,
            app_user.c.deletion_requested_at,
        )
        .join(app_user, app_user.c.id == api_key.c.owner_id)
        .where(api_key.c.public_id == presented.public_id)
    ).first()
    if row is None or not secret_matches(presented, row.secret_hash):
        return None
    if not key_is_active(expires_at=row.expires_at, revoked_at=row.revoked_at, now=now):
        logger.info("chave recusada por expiração ou revogação: %s", presented.public_id)
        return None
    if row.status != ACTIVE_STATUS or row.deletion_requested_at is not None:
        logger.info("chave recusada por conta bloqueada ou em exclusão: %s", presented.public_id)
        return None
    _record_use(session, row.id, now)
    return Principal(owner_id=row.owner_id, scopes=_known_scopes(row.scopes), via="api_key")


def find_account(session: Session, owner_id: UUID) -> Account | None:
    row = session.execute(
        select(app_user.c.id, app_user.c.display_name).where(app_user.c.id == owner_id)
    ).first()
    return None if row is None else Account(id=row.id, display_name=row.display_name)


def _record_use(session: Session, key_id: UUID, now: datetime) -> None:
    # UPDATE condicional: requisições simultâneas não gravam todas, e nenhuma lê antes.
    session.execute(
        update(api_key)
        .where(
            api_key.c.id == key_id,
            or_(
                api_key.c.last_used_at.is_(None),
                api_key.c.last_used_at <= now - LAST_USED_RESOLUTION,
            ),
        )
        .values(last_used_at=now)
    )


def _known_scopes(stored: Iterable[str]) -> frozenset[Scope]:
    # Um escopo gravado que este código não conhece (versão mais nova durante o deploy)
    # não concede nada: falhar fechado.
    known = {scope.value for scope in Scope}
    return frozenset(Scope(name) for name in stored if name in known)


# --- Fatia 6: conta e chaves pela sessão web --------------------------------------------------
# Contrato fixado na Fase 0; os corpos são da Frente C.

# Teto de chaves ativas por conta, garantido com a linha da conta travada (`FOR UPDATE`).
MAX_ACTIVE_KEYS = 5
# Revogadas e vencidas também aparecem em `GET /api-keys`; o teto mantém a consulta limitada.
MAX_LISTED_KEYS = 100
MAX_LISTED_ACCOUNTS = 1000


@dataclass(frozen=True)
class KeySummary:
    """Metadados de uma chave para `GET /api-keys`; nunca o hash nem o segredo."""

    id: UUID
    public_id: str
    scopes: frozenset[Scope]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ActiveKeyLimitError(Exception):
    """A conta já tem `MAX_ACTIVE_KEYS` chaves ativas: 409 `STATE_CONFLICT`."""


def block_account(session: Session, owner_id: UUID, now: datetime) -> bool:
    """Bloqueia e revoga sessões e chaves; False se a conta não existir. Idempotente."""
    blocked = session.execute(
        update(app_user)
        .where(app_user.c.id == owner_id)
        .values(status=BLOCKED_STATUS)
        .returning(app_user.c.id)
    ).first()
    if blocked is None:
        return False
    sessions.revoke_all_credentials(session, owner_id, now)
    return True


def unblock_account(session: Session, owner_id: UUID) -> bool:
    """Libera a conta (inclusive a pendente do cadastro fechado); False se não existir.

    Sessões e chaves revogadas no bloqueio continuam revogadas: a pessoa entra de novo.
    """
    unblocked = session.execute(
        update(app_user)
        .where(app_user.c.id == owner_id)
        .values(status=ACTIVE_STATUS)
        .returning(app_user.c.id)
    ).first()
    return unblocked is not None


def request_account_deletion(session: Session, owner_id: UUID, now: datetime) -> None:
    """Marca a exclusão e revoga sessões e chaves; repetir mantém o primeiro pedido.

    O prazo de 24 h da purga (1.8) conta do primeiro pedido, por isso o instante não muda.
    """
    marked = session.execute(
        update(app_user)
        .where(app_user.c.id == owner_id)
        .values(deletion_requested_at=func.coalesce(app_user.c.deletion_requested_at, now))
        .returning(app_user.c.id)
    ).first()
    if marked is None:
        raise UnknownAccountError(f"conta {owner_id} não existe")
    sessions.revoke_all_credentials(session, owner_id, now)


def issue_key_for_session(
    session: Session, *, owner_id: UUID, scopes: frozenset[Scope], days: int, now: datetime
) -> IssuedKey:
    """Emite chave pela sessão web; acima do teto levanta `ActiveKeyLimitError`.

    A linha da conta fica travada (`FOR UPDATE`) da contagem até o INSERT: duas emissões
    simultâneas se enfileiram, e a segunda conta a chave da primeira (sem write skew). A mesma
    trava enfileira a emissão atrás de um bloqueio ou pedido de exclusão, que a recusa.
    """
    account = session.execute(
        select(app_user.c.id)
        .where(
            app_user.c.id == owner_id,
            app_user.c.status == ACTIVE_STATUS,
            app_user.c.deletion_requested_at.is_(None),
        )
        .with_for_update()
    ).first()
    if account is None:
        raise UnknownAccountError(f"conta {owner_id} não existe ou não está ativa")
    active = session.execute(
        select(func.count())
        .select_from(api_key)
        .where(
            api_key.c.owner_id == owner_id,
            api_key.c.revoked_at.is_(None),
            api_key.c.expires_at > now,
        )
    ).scalar_one()
    if active >= MAX_ACTIVE_KEYS:
        raise ActiveKeyLimitError(f"a conta já tem {MAX_ACTIVE_KEYS} chaves ativas")
    return issue_key(session, owner_id=owner_id, scopes=scopes, now=now, days=days)


def list_keys(session: Session, owner_id: UUID) -> list[KeySummary]:
    """Chaves da conta, das mais novas às mais antigas, até `MAX_LISTED_KEYS`."""
    rows = session.execute(
        select(
            api_key.c.id,
            api_key.c.public_id,
            api_key.c.scopes,
            api_key.c.created_at,
            api_key.c.expires_at,
            api_key.c.last_used_at,
            api_key.c.revoked_at,
        )
        .where(api_key.c.owner_id == owner_id)
        .order_by(api_key.c.created_at.desc(), api_key.c.id.desc())
        .limit(MAX_LISTED_KEYS)
    ).all()
    return [
        KeySummary(
            id=row.id,
            public_id=row.public_id,
            scopes=_known_scopes(row.scopes),
            created_at=row.created_at,
            expires_at=row.expires_at,
            last_used_at=row.last_used_at,
            revoked_at=row.revoked_at,
        )
        for row in rows
    ]


def revoke_owned_key(session: Session, owner_id: UUID, key_id: UUID, now: datetime) -> bool:
    """Revoga só chave da própria conta; False (→ 404) para inexistente ou alheia.

    O dono entra no WHERE, não numa checagem depois: a chave alheia nem é lida. Revogar de novo é
    sucesso e preserva o instante da primeira revogação.
    """
    revoked = session.execute(
        update(api_key)
        .where(api_key.c.id == key_id, api_key.c.owner_id == owner_id)
        .values(revoked_at=func.coalesce(api_key.c.revoked_at, now))
        .returning(api_key.c.id)
    ).first()
    return revoked is not None


# --- Listagem para o administrador (`gs-admin list-users`) ------------------------------------


@dataclass(frozen=True)
class AccountListing:
    """O que o administrador vê de uma conta; nunca o `subject` da identidade externa."""

    id: UUID
    issuer: str
    status: str
    created_at: datetime


def list_accounts(session: Session) -> list[AccountListing]:
    """Contas das mais novas às mais antigas, até `MAX_LISTED_ACCOUNTS`."""
    return _listings(session, _listing_query())


def list_pending_accounts(session: Session) -> list[AccountListing]:
    """Contas à espera de liberação no cadastro fechado (ADR 023).

    Pendente e bloqueada por abuso têm o mesmo `status`; a pendente nunca teve sessão.
    """
    ever_had_session = exists().where(web_session.c.user_id == app_user.c.id)
    query = _listing_query().where(app_user.c.status == BLOCKED_STATUS, ~ever_had_session)
    return _listings(session, query)


def _listing_query() -> Select[tuple[UUID, str, str, datetime]]:
    return (
        select(app_user.c.id, app_user.c.issuer, app_user.c.status, app_user.c.created_at)
        .order_by(app_user.c.created_at.desc(), app_user.c.id.desc())
        .limit(MAX_LISTED_ACCOUNTS)
    )


def _listings(
    session: Session, query: Select[tuple[UUID, str, str, datetime]]
) -> list[AccountListing]:
    return [
        AccountListing(id=row.id, issuer=row.issuer, status=row.status, created_at=row.created_at)
        for row in session.execute(query)
    ]
