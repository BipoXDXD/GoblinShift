"""Vagas de transferência simultânea (1.7), compartilhadas entre réplicas da API.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13 18:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Esta migração só acrescenta o
que o código anterior tolera; remoções vão numa migração de um deploy posterior.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transfer_slot",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "direction IN ('upload', 'download')", name=op.f("transfer_slot_direction_check")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("transfer_slot_owner_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("transfer_slot_pkey")),
    )
    op.create_index(op.f("transfer_slot_owner_id_idx"), "transfer_slot", ["owner_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("transfer_slot_owner_id_idx"), table_name="transfer_slot")
    op.drop_table("transfer_slot")
