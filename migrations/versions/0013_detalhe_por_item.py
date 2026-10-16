"""Detalhe por item da Run: `run_input.details` (avisos e contagens, sem texto do arquivo).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-16 02:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Coluna anulável e sem default: o
worker anterior não a escreve, e as Runs antigas ficam sem detalhe (a API responde listas vazias).
O CHECK só exige objeto; o conteúdo é validado pela coleta do worker antes de ser gravado (3.6).
Como os nomes e os parâmetros, é detalhe por item com retenção de 30 dias (1.7).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_input",
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        op.f("run_input_details_check"), "run_input", "jsonb_typeof(details) = 'object'"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("run_input_details_check"), "run_input", type_="check")
    op.drop_column("run_input", "details")
