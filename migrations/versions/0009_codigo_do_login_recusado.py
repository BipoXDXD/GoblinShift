"""Código de referência do login recusado (J-10): `audit_event.request_id`.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-15 22:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Coluna anulável e sem default: os
eventos antigos e os gravados fora de uma requisição (CLI) ficam sem código, e o código anterior,
que não a conhece, continua gravando. Sem índice: só o operador consulta por ela, à mão, numa
tabela que guarda 90 dias de eventos.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_event", sa.Column("request_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_event", "request_id")
