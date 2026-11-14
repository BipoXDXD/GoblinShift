"""`app_user.purge_attempted_at`: rodízio da purga das contas em exclusão (#442).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-14 08:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Coluna anulável e sem default: o código
anterior não a lê nem grava, e `ADD COLUMN` sem default não reescreve a tabela.

A manutenção marca a hora em cada conta que escolhe para o ciclo e escolhe primeiro as nunca
tentadas e depois as tentadas há mais tempo. Uma conta que falha sempre, ou que espera uma Run
não terminal, vai para o fim da fila em vez de ocupar o lote em todo ciclo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_user", sa.Column("purge_attempted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("app_user", "purge_attempted_at")
