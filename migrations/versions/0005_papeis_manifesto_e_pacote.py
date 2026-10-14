"""Papéis de artefato `manifest` e `package` (fatia 5, ADR 018).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-14 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só alarga o CHECK de
`artifact.role`: o código anterior nunca grava os papéis novos, e toda linha existente continua
válida. O downgrade só funciona enquanto nenhum artefato usar os papéis novos.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "artifact_role_check"


def upgrade() -> None:
    op.drop_constraint(op.f(_CHECK), "artifact", type_="check")
    op.create_check_constraint(
        op.f(_CHECK), "artifact", "role IN ('output', 'report', 'manifest', 'package')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_CHECK), "artifact", type_="check")
    op.create_check_constraint(op.f(_CHECK), "artifact", "role IN ('output', 'report')")
