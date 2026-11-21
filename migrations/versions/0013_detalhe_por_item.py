"""Detalhe por item da Run: `run_input.details` (avisos e contagens, sem texto do arquivo).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-16 02:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Coluna anulável e sem default: o
worker anterior não a escreve, e as Runs antigas ficam sem detalhe (a API responde listas vazias).
O CHECK só exige objeto; o conteúdo é validado pela coleta do worker antes de ser gravado (3.6).
Como os nomes e os parâmetros, é detalhe por item com retenção de 30 dias (1.7).

O downgrade apaga o detalhe por item (`run_input.details`) e só roda com
`GS_ALLOW_LOSSY_DOWNGRADE=1` se houver linhas.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # lock-ok: coluna nula sem default; astext_type só escolhe o tipo SQL do resultado de ->>
    op.add_column(
        "run_input",
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # lock-ok: details recém-criada e nula em todas as linhas; a varredura de run_input leva ms
    op.create_check_constraint(
        op.f("run_input_details_check"), "run_input", "jsonb_typeof(details) = 'object'"
    )


def downgrade() -> None:
    if os.environ.get("GS_ALLOW_LOSSY_DOWNGRADE") != "1":
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT FROM run_input WHERE details IS NOT NULL) "
            "THEN RAISE EXCEPTION 'o downgrade da 0013 apagaria o detalhe por item; "
            "GS_ALLOW_LOSSY_DOWNGRADE=1 aceita a perda'; END IF; END $$"
        )
    op.drop_constraint(op.f("run_input_details_check"), "run_input", type_="check")
    op.drop_column("run_input", "details")
