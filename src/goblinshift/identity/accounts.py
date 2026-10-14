"""Contas do administrador e chaves de API no banco (docs/GoblinShiftPlan.md, 3.6 e fatia 3).

Transaction scripts com a Session direto (3.2: identidade não tem agregado). Cada função
roda dentro da transação de quem a chama, que decide o commit.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid7

from sqlalchemy import Text, bindparam, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session

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

logger = logging.getLogger(__name__)

# Emissor das contas criadas pela CLI; o login por OIDC/OAuth da fatia 6 usa o do provedor.
ADMIN_ISSUER = "urn:goblinshift:admin"
ACTIVE_STATUS = "active"
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
    if session.execute(statement).first() is None:
        raise UnknownAccountError(f"conta {owner_id} não existe")
    return IssuedKey(public_id=key.public_id, token=key.token, expires_at=expires_at)


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
        )
        .join(app_user, app_user.c.id == api_key.c.owner_id)
        .where(api_key.c.public_id == presented.public_id)
    ).first()
    if row is None or not secret_matches(presented, row.secret_hash):
        return None
    if not key_is_active(expires_at=row.expires_at, revoked_at=row.revoked_at, now=now):
        logger.info("chave recusada por expiração ou revogação: %s", presented.public_id)
        return None
    if row.status != ACTIVE_STATUS:
        logger.info("chave recusada por conta bloqueada: %s", presented.public_id)
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
