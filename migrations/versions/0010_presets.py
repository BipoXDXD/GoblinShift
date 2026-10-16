"""Presets: tabela `preset` e `run.preset_id` com `ON DELETE SET NULL (preset_id)`.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-16 00:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Tabela nova e coluna anulável sem
default: o código anterior não as conhece e continua funcionando. A FK sobre a coluna recém-criada
não precisa de `NOT VALID`: com `preset_id` nulo em todas as linhas, a verificação é imediata.
O índice parcial atende à FK quando um preset é excluído; a tabela de Runs é pequena (retenção de
90 dias), então criá-lo na transação, sem `CONCURRENTLY`, basta.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUN_PRESET_FK = "run_owner_id_preset_id_fkey"


def upgrade() -> None:
    op.create_table(
        "preset",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("recipe_id", sa.Text(), nullable=False),
        sa.Column("recipe_version", sa.Integer(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 100", name=op.f("preset_name_check")),
        sa.CheckConstraint("recipe_version >= 1", name=op.f("preset_recipe_version_check")),
        sa.CheckConstraint("revision >= 1", name=op.f("preset_revision_check")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["app_user.id"], name=op.f("preset_owner_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("preset_pkey")),
        sa.UniqueConstraint("owner_id", "id", name=op.f("preset_owner_id_id_key")),
        sa.UniqueConstraint("owner_id", "name", name=op.f("preset_owner_id_name_key")),
    )
    op.add_column("run", sa.Column("preset_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        _RUN_PRESET_FK,
        "run",
        "preset",
        ["owner_id", "preset_id"],
        ["owner_id", "id"],
        ondelete="SET NULL (preset_id)",
    )
    op.create_index(
        "run_preset_id_idx",
        "run",
        ["preset_id"],
        unique=False,
        postgresql_where=sa.text("preset_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("run_preset_id_idx", table_name="run")
    op.drop_constraint(_RUN_PRESET_FK, "run", type_="foreignkey")
    op.drop_column("run", "preset_id")
    op.drop_table("preset")
