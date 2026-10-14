"""Tabelas da identidade (docs/GoblinShiftPlan.md, 3.3).

Na fatia 3 só existem contas criadas pelo administrador e chaves de API; sessões web e
auditoria entram na fatia 6.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY

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
    UniqueConstraint("issuer", "subject"),
    CheckConstraint("status IN ('active', 'blocked')", name="status"),
    CheckConstraint("length(display_name) BETWEEN 1 AND 200", name="display_name"),
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
