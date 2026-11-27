"""CHECK dos códigos de erro e de rejeição por item, gerada do catálogo (#442, 23c).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-21 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só constraints novas, que o código
anterior já cumpre: o worker e a API só gravam códigos de `ErrorCode` (`runs/lifecycle.py`) em
`run.stop_reason`, `run.error_code` e `attempt.error_code`, e a coleta recusa no `report.json`
qualquer código de item fora de `ItemRejectionCode` (`processing/recipe.py`). Um valor fora do
catálogo derruba a leitura da Run (`runs/store.py`), que converte a coluna no enum.

As listas abaixo são cópia literal dos enums de hoje (decisão D3 do plano): a migração não importa
`goblinshift`, porque roda anos depois contra o código de outra época. No modelo, a CHECK nasce do
enum (`runs/tables.py`, `_sql_in`); um código novo muda o DDL do modelo e o teste de drift do schema
quebra até existir a migração que reescreve a CHECK. NULL continua aceito (CHECK com NULL passa).

As quatro CHECKs entram `NOT VALID` (ADR 057; decisão D4): só gravam no catálogo, sem varrer as
tabelas, e valem para toda linha nova a partir daqui. O `VALIDATE` fica na 0023, numa transação
própria. Não há reparo: o que fazer com um valor fora do catálogo é decisão do dono (D3). O
downgrade só remove as constraints, sem perder dado.

Antes do merge, o operador conta em produção os valores fora das listas; tudo zero, a 0023 valida
sem erro:

    SELECT (SELECT count(*) FROM run
             WHERE stop_reason NOT IN ('NO_VALID_OUTPUTS', 'OUTPUT_LIMIT_EXCEEDED',
               'REPORT_LIMIT_EXCEEDED', 'RESOURCE_LIMIT_EXCEEDED', 'INPUT_LIMIT_EXCEEDED',
               'PDF_INVALID', 'PDF_UNSUPPORTED_FEATURE', 'PDF_PAGE_NOT_FOUND',
               'EXECUTOR_UNAVAILABLE', 'QUEUE_EXPIRED', 'DEADLINE_EXCEEDED', 'INFRA_POISON',
               'INPUT_TAMPERED', 'CANCELED')) AS run_stop_reason,
           (SELECT count(*) FROM run
             WHERE error_code NOT IN ('NO_VALID_OUTPUTS', 'OUTPUT_LIMIT_EXCEEDED',
               'REPORT_LIMIT_EXCEEDED', 'RESOURCE_LIMIT_EXCEEDED', 'INPUT_LIMIT_EXCEEDED',
               'PDF_INVALID', 'PDF_UNSUPPORTED_FEATURE', 'PDF_PAGE_NOT_FOUND',
               'EXECUTOR_UNAVAILABLE', 'QUEUE_EXPIRED', 'DEADLINE_EXCEEDED', 'INFRA_POISON',
               'INPUT_TAMPERED', 'CANCELED')) AS run_error_code,
           (SELECT count(*) FROM attempt
             WHERE error_code NOT IN ('NO_VALID_OUTPUTS', 'OUTPUT_LIMIT_EXCEEDED',
               'REPORT_LIMIT_EXCEEDED', 'RESOURCE_LIMIT_EXCEEDED', 'INPUT_LIMIT_EXCEEDED',
               'PDF_INVALID', 'PDF_UNSUPPORTED_FEATURE', 'PDF_PAGE_NOT_FOUND',
               'EXECUTOR_UNAVAILABLE', 'QUEUE_EXPIRED', 'DEADLINE_EXCEEDED', 'INFRA_POISON',
               'INPUT_TAMPERED', 'CANCELED')) AS attempt_error_code,
           (SELECT count(*) FROM run_input
             WHERE error_code NOT IN ('UNSUPPORTED_FORMAT', 'UNSUPPORTED_BIT_DEPTH',
               'UNSUPPORTED_COLOR_PROFILE', 'IMAGE_TOO_LARGE', 'CSV_STRUCTURE_INVALID',
               'FIELD_LIMIT_EXCEEDED', 'MEDIA_TOO_LARGE', 'AUDIO_LIMIT_EXCEEDED',
               'AUDIO_STREAM_NOT_FOUND')) AS run_input_error_code;

(`NOT IN` com lista literal ignora NULL, como a CHECK.)
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Congeladas em 2026-11-21: `ErrorCode` (14) e `ItemRejectionCode` (9), na ordem dos enums.
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
    # ACCESS EXCLUSIVE de um instante em cada tabela, sob o lock_timeout do ADR 057.
    # lock-ok: CHECK NOT VALID só grava no catálogo, sem varrer run
    op.create_check_constraint(
        op.f("run_stop_reason_check"), "run", _STOP_REASONS, postgresql_not_valid=True
    )
    # lock-ok: CHECK NOT VALID só grava no catálogo, sem varrer run
    op.create_check_constraint(
        op.f("run_error_code_check"), "run", _ERROR_CODES, postgresql_not_valid=True
    )
    # lock-ok: CHECK NOT VALID só grava no catálogo, sem varrer attempt
    op.create_check_constraint(
        op.f("attempt_error_code_check"), "attempt", _ERROR_CODES, postgresql_not_valid=True
    )
    # lock-ok: CHECK NOT VALID só grava no catálogo, sem varrer run_input
    op.create_check_constraint(
        op.f("run_input_error_code_check"),
        "run_input",
        _ITEM_REJECTION_CODES,
        postgresql_not_valid=True,
    )


def downgrade() -> None:
    op.drop_constraint(op.f("run_input_error_code_check"), "run_input", type_="check")
    op.drop_constraint(op.f("attempt_error_code_check"), "attempt", type_="check")
    op.drop_constraint(op.f("run_error_code_check"), "run", type_="check")
    op.drop_constraint(op.f("run_stop_reason_check"), "run", type_="check")
