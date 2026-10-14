"""Limite de requisições por conta e classe de rota (fatia 8): tabela `request_window`.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-14 21:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só uma tabela nova, que o código
anterior ignora; durante o rollout, a API velha não conta requisições (ADR 028).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_window",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("request_class", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "request_class IN ('read', 'command', 'run_create')",
            name=op.f("request_window_request_class_check"),
        ),
        sa.CheckConstraint("request_count > 0", name=op.f("request_window_request_count_check")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("request_window_owner_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "owner_id", "request_class", "window_start", name=op.f("request_window_pkey")
        ),
    )


def downgrade() -> None:
    op.drop_table("request_window")
