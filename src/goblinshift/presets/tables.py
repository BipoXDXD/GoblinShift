"""Tabela de presets (docs/GoblinShiftPlan.md, 3.3).

Preset é configuração privada da conta: some junto com ela (FK em cascata, como a
`request_window` do ADR 028), porque são no máximo 100 linhas por conta e não há o que auditar
depois da exclusão. A Run guarda `preset_id` com `ON DELETE SET NULL (preset_id)`, então excluir
um preset não altera o histórico.
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from goblinshift.platform.db import metadata

preset = Table(
    "preset",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("owner_id", Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False),
    Column("name", Text, nullable=False),
    Column("recipe_id", Text, nullable=False),
    Column("recipe_version", Integer, nullable=False),
    # Parâmetros com os defaults da versão aplicados, na forma canônica do catálogo.
    Column("parameters", JSONB, nullable=False),
    # Controle otimista: o ETag é derivado daqui, e cada substituição soma 1.
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("owner_id", "id"),
    # Chave natural; o índice também serve à FK de `owner_id` e à listagem por nome.
    UniqueConstraint("owner_id", "name"),
    CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name"),
    CheckConstraint("recipe_version >= 1", name="recipe_version"),
    CheckConstraint("revision >= 1", name="revision"),
)
