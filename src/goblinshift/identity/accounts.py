"""Contas do administrador e chaves de API no banco (docs/GoblinShiftPlan.md, 3.6 e fatia 3).

Transaction scripts com a Session direto (3.2: identidade não tem agregado). Cada função
roda dentro da transação de quem a chama, que decide o commit.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid7

from sqlalchemy import (
    Select,
    Text,
    and_,
    bindparam,
    case,
    func,
    insert,
    or_,
    select,
    update,
)
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
from goblinshift.identity.tables import api_key, app_user
from goblinshift.platform.telemetry import UserHasher
from goblinshift.processing.names import has_unsafe_char

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
class AcceptedTerms:
    version: str
    accepted_at: datetime


@dataclass(frozen=True)
class Account:
    id: UUID
    display_name: str
    issuer: str
    accepted_terms: AcceptedTerms | None
    # `active` ou `blocked` (CHECK `app_user_status`).
    status: str
    created_at: datetime

    def needs_terms_acceptance(self, current_version: str) -> bool:
        """Conta do login sem o aceite da versão vigente (1.8; ADR 029).

        As contas do operador, criadas pelo `gs-admin` (aceitação, carga), não aceitam termos: o
        operador é quem os publica.
        """
        if self.issuer == ADMIN_ISSUER:
            return False
        return self.accepted_terms is None or self.accepted_terms.version != current_version


@dataclass(frozen=True)
class IssuedKey:
    """Chave emitida. `token` só existe aqui, para ser exibido uma vez."""

    id: UUID
    public_id: str
    token: str = field(repr=False)
    expires_at: datetime


class UnknownAccountError(LookupError):
    pass


class SessionNoLongerValidError(LookupError):
    """A sessão que pede a chave foi revogada, venceu, é de outra conta ou não existe."""


class InvalidDisplayNameError(ValueError):
    pass


def create_admin_user(session: Session, display_name: str) -> UUID:
    name = display_name.strip()
    if not 1 <= len(name) <= MAX_DISPLAY_NAME_CHARS:
        raise InvalidDisplayNameError(f"o nome deve ter de 1 a {MAX_DISPLAY_NAME_CHARS} caracteres")
    if has_unsafe_char(name):
        raise InvalidDisplayNameError(
            "o nome não aceita caracteres de controle nem de formato invisível"
        )
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
    idem_key: str | None = None,
) -> IssuedKey:
    """Grava a chave sem teto nem trava; a emissão pela sessão passa por `issue_key_for_session`.

    `idem_key` só é gravada: quem a passa já a procurou sob a trava da conta (`_issue_locked`).
    """
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
        bindparam("idem_key", idem_key, type_=Text),
    ).where(app_user.c.id == owner_id)
    statement = (
        insert(api_key)
        .from_select(
            [
                "owner_id",
                "public_id",
                "secret_hash",
                "scopes",
                "created_at",
                "expires_at",
                "idem_key",
            ],
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


def authenticate_api_key(
    session: Session, token: str, now: datetime, *, hasher: UserHasher
) -> Principal | None:
    """Devolve o Principal da chave, ou None se ela for malformada, desconhecida ou inválida.

    Registrar o uso faz parte de autenticar: é o que alimenta o "último uso" da chave. A recusa
    de chave conhecida vai ao log com o `user.hash` do dono e nunca com o `public_id`, a parte
    estável do token.
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
        _log_refused_key(hasher, row.owner_id, "expired_or_revoked")
        return None
    if row.status != ACTIVE_STATUS or row.deletion_requested_at is not None:
        _log_refused_key(hasher, row.owner_id, "account_unavailable")
        return None
    _record_use(session, row.id, now)
    return Principal(owner_id=row.owner_id, scopes=_known_scopes(row.scopes), via="api_key")


def _log_refused_key(
    hasher: UserHasher, owner_id: UUID, reason: Literal["expired_or_revoked", "account_unavailable"]
) -> None:
    logger.info(
        "chave de API recusada",
        extra={"fields": {"user.hash": hasher.hash(owner_id), "reason": reason}},
    )


def find_account(session: Session, owner_id: UUID) -> Account | None:
    row = session.execute(
        select(
            app_user.c.id,
            app_user.c.display_name,
            app_user.c.issuer,
            app_user.c.terms_version,
            app_user.c.terms_accepted_at,
            app_user.c.status,
            app_user.c.created_at,
        ).where(app_user.c.id == owner_id)
    ).first()
    if row is None:
        return None
    # O CHECK `app_user_terms` garante versão e data juntas, ou nenhuma das duas.
    accepted = (
        None
        if row.terms_version is None
        else AcceptedTerms(version=row.terms_version, accepted_at=row.terms_accepted_at)
    )
    return Account(
        id=row.id,
        display_name=row.display_name,
        issuer=row.issuer,
        accepted_terms=accepted,
        status=row.status,
        created_at=row.created_at,
    )


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

# Teto de chaves ativas por conta, garantido com a linha da conta travada (`FOR NO KEY UPDATE`).
MAX_ACTIVE_KEYS = 5
# Teto de chaves guardadas (ativas, revogadas e vencidas) por conta, sob a mesma trava: sem ele,
# emitir e revogar em laço enche a tabela, porque a revogada só sai `KEY_RETENTION` depois do fim.
MAX_STORED_KEYS = 100
# Revogada ou vencida sai da tabela 30 dias depois do fim (o primeiro entre os dois), pela
# manutenção (`identity.purge.delete_inactive_api_keys`).
KEY_RETENTION = timedelta(days=30)
# Revogadas e vencidas também aparecem em `GET /api-keys`, depois das ativas; o teto mantém a
# consulta limitada.
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


class KeyAlreadyIssuedError(Exception):
    """A `Idempotency-Key` já emitiu uma chave desta conta: 409 `STATE_CONFLICT` com o id dela.

    Vale para a chave em qualquer estado, ativa, revogada ou vencida, e sem comparar o pedido: o
    segredo só existiu na primeira resposta e não é guardado, então não há o que repetir.
    """

    def __init__(self, key_id: UUID, public_id: str) -> None:
        super().__init__(f"a Idempotency-Key já emitiu a chave {public_id} (id {key_id})")
        self.key_id = key_id
        self.public_id = public_id


class StoredKeyLimitError(Exception):
    """A conta já guarda `MAX_STORED_KEYS` chaves: 409 `STATE_CONFLICT`.

    Revogar não abre vaga; ela abre quando a manutenção apaga as inativas (`KEY_RETENTION`).
    """


def block_account(session: Session, owner_id: UUID, now: datetime) -> bool:
    """Bloqueia e revoga sessões e chaves; False se a conta não existir. Idempotente."""
    blocked = session.execute(
        update(app_user)
        .where(app_user.c.id == owner_id)
        .values(status=BLOCKED_STATUS, awaiting_approval=False)
        .returning(app_user.c.id)
    ).first()
    if blocked is None:
        return False
    sessions.revoke_all_credentials(session, owner_id, now)
    return True


def unblock_account(session: Session, owner_id: UUID) -> bool:
    """Libera a conta (inclusive a pendente do cadastro fechado); False se não existir.

    Conta com exclusão pedida também dá False: o pedido do titular é irrevogável e a purga já
    está a caminho, então ela nunca volta a `active`. Sessões e chaves revogadas no bloqueio
    continuam revogadas: a pessoa entra de novo.
    """
    unblocked = session.execute(
        update(app_user)
        .where(app_user.c.id == owner_id, app_user.c.deletion_requested_at.is_(None))
        .values(status=ACTIVE_STATUS, awaiting_approval=False)
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
    session: Session,
    *,
    owner_id: UUID,
    session_id: UUID,
    scopes: frozenset[Scope],
    days: int,
    now: datetime,
    idem_key: str | None = None,
) -> IssuedKey:
    """Emite chave pela sessão web `session_id`, dentro dos dois tetos da conta.

    A linha da conta fica travada (`FOR NO KEY UPDATE`) da reconferência até o INSERT. A mesma
    trava enfileira a emissão atrás de um bloqueio, pedido de exclusão ou `logout-all`
    (`sessions.revoke_all_credentials`), que a recusam; o `FOR KEY SHARE` das FKs para a conta
    (login, criação de Run, auditoria) não espera por ela. Conta inativa levanta
    `UnknownAccountError`; sessão que não existe, é de outra conta, foi revogada ou venceu,
    `SessionNoLongerValidError`. A `idem_key` e os tetos ficam em `_issue_locked`.
    """
    account = session.execute(
        select(app_user.c.id)
        .where(
            app_user.c.id == owner_id,
            app_user.c.status == ACTIVE_STATUS,
            app_user.c.deletion_requested_at.is_(None),
        )
        .with_for_update(key_share=True)
    ).first()
    if account is None:
        raise UnknownAccountError(f"conta {owner_id} não existe ou não está ativa")
    # O Principal foi lido noutra transação: sem reconferir aqui, a emissão que esperou o
    # `logout-all` sairia com chave ativa de uma sessão já revogada.
    if not sessions.session_still_valid(session, session_id=session_id, owner_id=owner_id, now=now):
        raise SessionNoLongerValidError(
            f"a sessão {session_id} não vale mais para a conta {owner_id}"
        )
    return _issue_locked(
        session, owner_id=owner_id, scopes=scopes, days=days, now=now, idem_key=idem_key
    )


def _issue_locked(
    session: Session,
    *,
    owner_id: UUID,
    scopes: frozenset[Scope],
    days: int,
    now: datetime,
    idem_key: str | None,
) -> IssuedKey:
    """Miolo da emissão, com a linha da conta já travada por quem chama.

    Nesta ordem: a `idem_key` que já emitiu chave da conta levanta `KeyAlreadyIssuedError`; com
    `MAX_ACTIVE_KEYS` ativas, `ActiveKeyLimitError`; com `MAX_STORED_KEYS` guardadas, contando
    revogadas e vencidas, `StoredKeyLimitError`. A busca vem antes dos tetos para que a repetição
    com o teto cheio responda a repetição. Como a trava vale da busca até o INSERT, duas emissões
    simultâneas se enfileiram, e a segunda vê a chave da primeira (sem write skew); o índice único
    `(owner_id, idem_key)` fica de rede.
    """
    if idem_key is not None:
        previous = session.execute(
            select(api_key.c.id, api_key.c.public_id).where(
                api_key.c.owner_id == owner_id, api_key.c.idem_key == idem_key
            )
        ).first()
        if previous is not None:
            raise KeyAlreadyIssuedError(previous.id, previous.public_id)
    active, stored = session.execute(
        select(
            func.count().filter(api_key.c.revoked_at.is_(None), api_key.c.expires_at > now),
            func.count(),
        )
        .select_from(api_key)
        .where(api_key.c.owner_id == owner_id)
    ).one()
    if active >= MAX_ACTIVE_KEYS:
        raise ActiveKeyLimitError(f"a conta já tem {MAX_ACTIVE_KEYS} chaves ativas")
    if stored >= MAX_STORED_KEYS:
        raise StoredKeyLimitError(f"a conta já guarda {MAX_STORED_KEYS} chaves")
    return issue_key(
        session, owner_id=owner_id, scopes=scopes, now=now, days=days, idem_key=idem_key
    )


def list_keys(session: Session, owner_id: UUID, now: datetime) -> list[KeySummary]:
    """Chaves da conta até `MAX_LISTED_KEYS`: ativas primeiro, depois das mais novas às antigas.

    Revogadas e vencidas ficam na tabela até `KEY_RETENTION` depois do fim. A emissão pela sessão
    para em `MAX_STORED_KEYS`, que cabe inteiro na lista, mas o `gs-admin issue-key` não passa por
    esse teto; sem priorizar as ativas (no máximo `MAX_ACTIVE_KEYS` pela sessão), chaves mais novas
    tirariam da lista uma chave que ainda autentica, e o dono ficaria sem o id para revogá-la.
    """
    is_inactive = case(
        (and_(api_key.c.revoked_at.is_(None), api_key.c.expires_at > now), False), else_=True
    )
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
        .order_by(is_inactive, api_key.c.created_at.desc(), api_key.c.id.desc())
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


def find_account_by_user_hash(
    session: Session, user_hash: str, hasher: Callable[[UUID], str]
) -> UUID | None:
    """Conta cujo `user.hash` da telemetria (3.7) é `user_hash`; None se nenhuma for.

    O HMAC não se inverte: confere conta por conta. Serve ao roteiro de abuso, rodado à mão
    sobre as poucas contas de uma VPS (16 bytes por id).
    """
    ids = session.execute(select(app_user.c.id)).scalars()
    return next((owner_id for owner_id in ids if hasher(owner_id) == user_hash), None)


def list_pending_accounts(session: Session) -> list[AccountListing]:
    """Contas à espera de liberação no cadastro fechado (ADR 023).

    Pendente e bloqueada por abuso têm o mesmo `status`; a marca `awaiting_approval` as separa.
    """
    query = _listing_query().where(
        app_user.c.status == BLOCKED_STATUS, app_user.c.awaiting_approval.is_(True)
    )
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
