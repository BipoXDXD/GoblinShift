"""Valida as CHECKs dos códigos do catálogo da 0022 (#442, 23c).

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-21 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. `VALIDATE CONSTRAINT` das quatro CHECKs,
numa transação própria (ADR 057; decisão D4 do plano). Cada `VALIDATE` toma
`SHARE UPDATE EXCLUSIVE`, que não bloqueia leitura nem escrita da API, e varre a tabela.

Sem reparo: um valor fora do catálogo é decisão do dono (D3), e a migração falha em vez de
reescrevê-lo. A consulta que conta esses valores em produção está na docstring da 0022.

O downgrade volta ao catálogo da 0022: remove as quatro CHECKs e as recria `NOT VALID`, sem perder
dado.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Cópia literal das listas da 0022, para o downgrade recriar as CHECKs como lá.
_ERROR_CODES = (
    "error_code IN ('NO_VALID_OUTPUTS', 'OUTPUT_LIMIT_EXCEEDED', 'REPORT_LIMIT_EXCEEDED', "
    "'RESOURCE_LIMIT_EXCEEDED', 'INPUT_LIMIT_EXCEEDED', 'PDF_INVALID', 'PDF_UNSUPPORTED_FEATURE', "
    "'PDF_PAGE_NOT_FOUND', 'EXECUTOR_UNAVAILABLE', 'QUEUE_EXPIRED', 'DEADLINE_EXCEEDED', "
    "'INFRA_POISON', 'INPUT_TAMPERED', 'CANCELED')"
)
_STOP_REASONS = (
    "stop_reason IN ('NO_VALID_OUTPUTS', 'OUTPUT_LIMIT_EXCEEDED', 'REPORT_LIMIT_EXCEEDED', "
    "'RESOURCE_LIMIT_EXCEEDED', 'INPUT_LIMIT_EXCEEDED', 'PDF_INVALID', 'PDF_UNSUPPORTED_FEATURE', "
    "'PDF_PAGE_NOT_FOUND', 'EXECUTOR_UNAVAILABLE', 'QUEUE_EXPIRED', 'DEADLINE_EXCEEDED', "
    "'INFRA_POISON', 'INPUT_TAMPERED', 'CANCELED')"
)
_ITEM_REJECTION_CODES = (
    "error_code IN ('UNSUPPORTED_FORMAT', 'UNSUPPORTED_BIT_DEPTH', 'UNSUPPORTED_COLOR_PROFILE', "
    "'IMAGE_TOO_LARGE', 'CSV_STRUCTURE_INVALID', 'FIELD_LIMIT_EXCEEDED', 'MEDIA_TOO_LARGE', "
    "'AUDIO_LIMIT_EXCEEDED', 'AUDIO_STREAM_NOT_FOUND')"
)


def upgrade() -> None:
    # lock-ok: VALIDATE toma SHARE UPDATE EXCLUSIVE, que não bloqueia leitura nem escrita
    op.execute("ALTER TABLE run VALIDATE CONSTRAINT run_stop_reason_check")
    # lock-ok: VALIDATE toma SHARE UPDATE EXCLUSIVE, que não bloqueia leitura nem escrita
    op.execute("ALTER TABLE run VALIDATE CONSTRAINT run_error_code_check")
    # lock-ok: VALIDATE toma SHARE UPDATE EXCLUSIVE, que não bloqueia leitura nem escrita
    op.execute("ALTER TABLE attempt VALIDATE CONSTRAINT attempt_error_code_check")
    # lock-ok: VALIDATE toma SHARE UPDATE EXCLUSIVE, que não bloqueia leitura nem escrita
    op.execute("ALTER TABLE run_input VALIDATE CONSTRAINT run_input_error_code_check")


def downgrade() -> None:
    op.drop_constraint(op.f("run_stop_reason_check"), "run", type_="check")
    op.create_check_constraint(
        op.f("run_stop_reason_check"), "run", _STOP_REASONS, postgresql_not_valid=True
    )
    op.drop_constraint(op.f("run_error_code_check"), "run", type_="check")
    op.create_check_constraint(
        op.f("run_error_code_check"), "run", _ERROR_CODES, postgresql_not_valid=True
    )
    op.drop_constraint(op.f("attempt_error_code_check"), "attempt", type_="check")
    op.create_check_constraint(
        op.f("attempt_error_code_check"), "attempt", _ERROR_CODES, postgresql_not_valid=True
    )
    op.drop_constraint(op.f("run_input_error_code_check"), "run_input", type_="check")
    op.create_check_constraint(
        op.f("run_input_error_code_check"),
        "run_input",
        _ITEM_REJECTION_CODES,
        postgresql_not_valid=True,
    )
