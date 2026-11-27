"""Valida o CHECK `attempt_yielded_after_start_check` criado na 0029 (#442).

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-27 09:05:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só valida a constraint já aplicada em
linhas novas pela 0029.

O `VALIDATE CONSTRAINT` roda sob `SHARE UPDATE EXCLUSIVE`, sem bloquear leituras nem escritas em
`attempt`. A instrução é idempotente: se o retry do `lock_timeout` (ADR 057) repetir esta revisão,
validar uma constraint já válida continua seguro.

O downgrade mantém o estado válido: `0029` já aceita linhas validadas e o downgrade da `0029`
remove a constraint e a coluna.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # lock-ok: VALIDATE CONSTRAINT pega só SHARE UPDATE EXCLUSIVE (não bloqueia SELECT nem DML)
    op.execute("ALTER TABLE attempt VALIDATE CONSTRAINT attempt_yielded_after_start_check")


def downgrade() -> None:
    # Validação não muda o comportamento da 0029; o downgrade da 0029 remove a constraint.
    pass
