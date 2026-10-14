"""Tabelas da identidade (docs/GoblinShiftPlan.md, 3.3).

Contas, chaves de API (fatia 3), sessões web, estado transitório do login e auditoria
(fatia 6, migração 0006).
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, CIDR

from goblinshift.platform.db import metadata

app_user = Table(
    "app_user",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    # Identidade é (emissor, sujeito), nunca e-mail (3.6). Contas do administrador usam um
    # emissor próprio até o login por OIDC/OAuth da fatia 6.
    Column("issuer", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Versão e data dos termos aceitos (1.8); o fluxo de aceite chega com a interface (fatia 7).
    Column("terms_version", Text),
    Column("terms_accepted_at", DateTime(timezone=True)),
    # `DELETE /me`: a conta deixa de autenticar na hora e a manutenção a purga em até 24 h.
    Column("deletion_requested_at", DateTime(timezone=True)),
    UniqueConstraint("issuer", "subject"),
    CheckConstraint("status IN ('active', 'blocked')", name="status"),
    CheckConstraint("length(display_name) BETWEEN 1 AND 200", name="display_name"),
    CheckConstraint("(terms_version IS NULL) = (terms_accepted_at IS NULL)", name="terms"),
    CheckConstraint("length(terms_version) BETWEEN 1 AND 64", name="terms_version"),
    Index(
        None, "deletion_requested_at", postgresql_where=text("deletion_requested_at IS NOT NULL")
    ),
)

api_key = Table(
    "api_key",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("owner_id", Uuid, ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False),
    # Identificador público, exibido e usado para localizar a chave antes de comparar o hash.
    Column("public_id", Text, nullable=False, unique=True),
    # SHA-256 do segredo de 256 bits: alta entropia dispensa hash lento (3.6).
    Column("secret_hash", LargeBinary(32), nullable=False),
    # Os escopos válidos ficam no código, não num CHECK: o conjunto cresce com a API.
    Column("scopes", ARRAY(Text), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    Column("last_used_at", DateTime(timezone=True)),
    Index(None, "owner_id"),
    CheckConstraint("octet_length(secret_hash) = 32", name="secret_hash"),
    CheckConstraint("cardinality(scopes) > 0", name="scopes"),
    CheckConstraint("expires_at > created_at", name="expires_at"),
)

# Sessão web opaca e revogável (3.6). O nome evita confusão com a Session do SQLAlchemy.
web_session = Table(
    "web_session",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("user_id", Uuid, ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False),
    # SHA-256 do segredo de 256 bits do cookie, como nas chaves de API.
    Column("secret_hash", LargeBinary(32), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Login no provedor: base do teto absoluto de 8 h e da autenticação recente de 10 min.
    Column("authenticated_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("idle_expires_at", DateTime(timezone=True), nullable=False),
    Column("absolute_expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    Index(None, "user_id"),
    Index(None, "absolute_expires_at", postgresql_where=text("revoked_at IS NULL")),
    CheckConstraint("octet_length(secret_hash) = 32", name="secret_hash"),
    # Comparadas a `authenticated_at`, que vem do mesmo relógio da aplicação: `created_at` usa o
    # do banco e não serve de base a prazos calculados no código.
    CheckConstraint("absolute_expires_at > authenticated_at", name="absolute_expires_at"),
    CheckConstraint("idle_expires_at <= absolute_expires_at", name="idle_expires_at"),
)

# Cache do Authlib (`state`, `nonce`, `code_verifier`) durante o login, com TTL de 10 min (3.6).
# O cookie `__Secure-gs_login` leva só o marcador; o conteúdo nunca sai do servidor (ADR 025).
login_state = Table(
    "login_state",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Index(None, "expires_at"),
    CheckConstraint("length(key) BETWEEN 1 AND 200", name="key"),
    CheckConstraint("length(value) <= 4096", name="value"),
)

# Eventos de segurança, guardados 90 dias (1.8). `user_id` não tem FK de propósito: o evento
# sobrevive à purga da conta (ADR 026). Os tipos repetem `AuditEventType`; um teste confere.
audit_event = Table(
    "audit_event",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("user_id", Uuid),
    Column("event_type", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    # IP truncado: /24 em IPv4 e /48 em IPv6 (3.6).
    Column("ip_prefix", CIDR),
    Column("occurred_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index(None, "occurred_at"),
    Index(None, "user_id", "occurred_at"),
    CheckConstraint(
        "event_type IN ('LOGIN_SUCCEEDED', 'LOGIN_FAILED', 'LOGOUT', 'LOGOUT_ALL', 'KEY_ISSUED', "
        "'KEY_REVOKED', 'REAUTH_REQUIRED', 'CSRF_FAILED', 'ACCESS_DENIED', "
        "'ACCOUNT_DELETION_REQUESTED', 'ACCOUNT_BLOCKED', 'ACCOUNT_UNBLOCKED')",
        name="event_type",
    ),
    CheckConstraint("outcome IN ('success', 'failure')", name="outcome"),
    CheckConstraint(
        "masklen(ip_prefix) = CASE family(ip_prefix) WHEN 4 THEN 24 ELSE 48 END",
        name="ip_prefix",
    ),
)
