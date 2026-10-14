"""Tabelas do livro-razão de capacidade (docs/GoblinShiftPlan.md, 3.3; ADR 023).

`capacity_pool` guarda o teto e o total reservado por escopo (global ou conta) e recurso;
`resource_reservation` é o livro-razão, uma linha por reserva, de onde a manutenção recalcula o
pool. Os valores dos CHECKs repetem `Resource` e `HolderKind`; um teste confere.
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)

from goblinshift.platform.db import metadata

capacity_pool = Table(
    "capacity_pool",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("scope", Text, nullable=False),
    Column("owner_id", Uuid, ForeignKey("app_user.id", ondelete="RESTRICT")),
    Column("resource", Text, nullable=False),
    # Bytes passam de 2^31 (4 GiB por conta): sempre bigint (3.3).
    Column("max_amount", BigInteger, nullable=False),
    Column("reserved", BigInteger, nullable=False, server_default=text("0")),
    # Um pool por (escopo, conta, recurso); NULLS NOT DISTINCT faz o global (sem conta) colidir.
    UniqueConstraint("scope", "owner_id", "resource", postgresql_nulls_not_distinct=True),
    Index(None, "owner_id"),
    CheckConstraint("scope IN ('global', 'account')", name="scope"),
    CheckConstraint("(scope = 'global') = (owner_id IS NULL)", name="owner"),
    CheckConstraint("resource IN ('storage_bytes')", name="resource"),
    CheckConstraint("reserved BETWEEN 0 AND max_amount", name="reserved"),
)

# `holder_id` não tem FK: o detentor é um upload, uma Run ou um objeto, conforme `holder_kind`.
# Associação polimórfica aceita de propósito num livro-razão interno, sem JOIN de negócio; a
# integridade vem da reconciliação da manutenção (ADR 023).
resource_reservation = Table(
    "resource_reservation",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("owner_id", Uuid, ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False),
    Column("holder_kind", Text, nullable=False),
    Column("holder_id", Uuid, nullable=False),
    Column("resource", Text, nullable=False),
    Column("amount", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("released_at", DateTime(timezone=True)),
    # Uma reserva viva por detentor e recurso: liberar é idempotente e transferir não duplica.
    Index(
        "resource_reservation_live_holder_idx",
        "holder_kind",
        "holder_id",
        "resource",
        unique=True,
        postgresql_where=text("released_at IS NULL"),
    ),
    Index(None, "owner_id"),
    CheckConstraint("holder_kind IN ('upload', 'run', 'object')", name="holder_kind"),
    CheckConstraint("resource IN ('storage_bytes')", name="resource"),
    CheckConstraint("amount >= 0", name="amount"),
)
