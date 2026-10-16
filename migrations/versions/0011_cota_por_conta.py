"""Override das cotas diárias por conta (1.7 e 1.8): tabela `account_quota`.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-16 01:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Tabela nova; o código anterior não a
lê e segue com os limites fixos de 1.7. NULL numa coluna é "sem override": vale o padrão das
settings. FK com CASCADE, exceção à regra do RESTRICT (3.3): a linha é configuração da conta,
sem histórico próprio, e sai com ela na purga do `DELETE /me`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_quota",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("runs_per_24h", sa.Integer(), nullable=True),
        sa.Column("input_bytes_per_24h", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("runs_per_24h > 0", name=op.f("account_quota_runs_per_24h_check")),
        sa.CheckConstraint(
            "input_bytes_per_24h > 0", name=op.f("account_quota_input_bytes_per_24h_check")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("account_quota_owner_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("owner_id", name=op.f("account_quota_pkey")),
    )


def downgrade() -> None:
    op.drop_table("account_quota")
