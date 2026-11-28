"""Coluna `run.preset_revision`: revisão do preset que a Run executou (#442).

Revision ID: 0033
Revises: 0031
Create Date: 2026-11-28 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só uma coluna anulável sem default, que
o código anterior ignora: o INSERT dele não a cita e grava nulo, e as Runs antigas ficam nulas.

`POST /presets/{id}/runs` passa a aceitar `If-Match` opcional e grava a revisão cujos parâmetros
copiou (65a). Sem CHECK: validar uma CHECK nova varreria `run` sob `ACCESS EXCLUSIVE`, e a revisão
vem do preset, que já impõe `revision >= 1`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run", sa.Column("preset_revision", sa.Integer()))


def downgrade() -> None:
    op.drop_column("run", "preset_revision")
