"""Valida os tetos de reserva criados `NOT VALID` na 0032 (#442, R5).

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-27 21:05:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só valida as constraints que a 0032 já
aplica às linhas novas: `account_quota.input_bytes_per_24h <= 2^50` e
`capacity_pool.max_amount <= 2^50`.

O `VALIDATE CONSTRAINT` roda sob `SHARE UPDATE EXCLUSIVE`, sem bloquear leituras nem escritas. Se
alguma linha antiga passar do teto, a validação falha e o deploy para: antes do merge, uma consulta
só de leitura na produção confere os máximos (R5; exige autorização do dono). A instrução é
idempotente: se o retry do `lock_timeout` (ADR 057) repetir esta revisão, validar de novo é seguro.

O downgrade mantém o estado válido: a 0032 aceita constraints validadas, e o downgrade dela as
remove.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # lock-ok: VALIDATE CONSTRAINT pega só SHARE UPDATE EXCLUSIVE (não bloqueia SELECT nem DML)
    op.execute(
        "ALTER TABLE account_quota "
        "VALIDATE CONSTRAINT account_quota_input_bytes_per_24h_ceiling_check"
    )
    # lock-ok: VALIDATE CONSTRAINT pega só SHARE UPDATE EXCLUSIVE (não bloqueia SELECT nem DML)
    op.execute(
        "ALTER TABLE capacity_pool VALIDATE CONSTRAINT capacity_pool_max_amount_ceiling_check"
    )


def downgrade() -> None:
    # Validação não muda o comportamento da 0032; o downgrade da 0032 remove as constraints.
    pass
