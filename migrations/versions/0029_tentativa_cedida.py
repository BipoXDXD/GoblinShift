"""Coluna `attempt.yielded_at` e CHECK NOT VALID para tentativa cedida no SIGTERM (#442).

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-27 09:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só uma coluna anulável sem default e
um CHECK `NOT VALID`, que o código anterior ignora.

No SIGTERM, o worker pode ceder uma tentativa longa cujo tempo restante passa da carência de
parada (`stop_grace_period`), parando o executor e devolvendo a execução para `retry_wait` sem
gastar tentativa física (`attempt.yielded_at IS NOT NULL`). O CHECK garante que só uma tentativa
que chegou a iniciar o executor pode ser marcada como cedida.

Esta revisão cria a coluna e o CHECK `NOT VALID` (Split Phase): o `ADD COLUMN` só toca no catálogo
e o `NOT VALID` evita varrer `attempt` sob `ACCESS EXCLUSIVE`. A validação do CHECK fica na 0030.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK_NAME = "attempt_yielded_after_start_check"


def upgrade() -> None:
    op.add_column("attempt", sa.Column("yielded_at", sa.DateTime(timezone=True)))
    # lock-ok: NOT VALID não varre `attempt`; só grava a regra para linhas novas/alteradas
    op.create_check_constraint(
        op.f(_CHECK_NAME),
        "attempt",
        "yielded_at IS NULL OR executor_started_at IS NOT NULL",
        postgresql_not_valid=True,
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_CHECK_NAME), "attempt", type_="check")
    op.drop_column("attempt", "yielded_at")
