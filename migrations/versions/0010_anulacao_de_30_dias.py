"""Anulação de 30 dias (1.7, "Retenção"): nome do upload e parâmetros da Run anuláveis.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-15 23:30:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Nada aqui é escrito pelo código atual:
as colunas só deixam de exigir valor, e o código anterior continua gravando e lendo como antes.
A anulação vem no deploy seguinte, pela manutenção. Os CHECKs restringem o NULL ao que já acabou,
para proteger quem ainda lê as colunas: o claim e a preparação da área de trabalho leem os
parâmetros e o nome das entradas de execuções vivas.

Os índices parciais cobrem só o que ainda falta anular ou apagar, como pede 1.7. As tabelas são
pequenas (uma VPS, poucas contas), então o `CREATE INDEX` comum, dentro da transação do Alembic,
basta; com tráfego grande seria `CONCURRENTLY`, fora de transação.

O downgrade devolve o NOT NULL preenchendo o que a manutenção já anulou: o nome volta como
"anonimizado" e os parâmetros como `{}`, porque o dado original não existe mais.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ANONYMIZED_NAME = "anonimizado"


def upgrade() -> None:
    op.alter_column("upload", "original_name", existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        op.f("upload_name_until_deleted_check"),
        "upload",
        "original_name IS NOT NULL OR deleted_at IS NOT NULL",
    )
    op.alter_column(
        "run", "parameters", existing_type=sa.dialects.postgresql.JSONB(), nullable=True
    )
    op.create_check_constraint(
        op.f("run_parameters_until_finished_check"),
        "run",
        "parameters IS NOT NULL OR finished_at IS NOT NULL",
    )
    op.create_index(
        "run_anonymize_idx",
        "run",
        ["finished_at"],
        postgresql_where=sa.text("parameters IS NOT NULL AND finished_at IS NOT NULL"),
    )
    op.create_index(
        "run_finished_at_idx",
        "run",
        ["finished_at"],
        postgresql_where=sa.text("finished_at IS NOT NULL"),
    )
    op.create_index(
        "upload_anonymize_idx",
        "upload",
        ["created_at"],
        postgresql_where=sa.text("original_name IS NOT NULL AND deleted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("upload_anonymize_idx", table_name="upload")
    op.drop_index("run_finished_at_idx", table_name="run")
    op.drop_index("run_anonymize_idx", table_name="run")
    op.drop_constraint(op.f("run_parameters_until_finished_check"), "run", type_="check")
    op.execute("UPDATE run SET parameters = '{}'::jsonb WHERE parameters IS NULL")
    op.alter_column(
        "run", "parameters", existing_type=sa.dialects.postgresql.JSONB(), nullable=False
    )
    op.drop_constraint(op.f("upload_name_until_deleted_check"), "upload", type_="check")
    op.execute(
        sa.text("UPDATE upload SET original_name = :name WHERE original_name IS NULL").bindparams(
            name=_ANONYMIZED_NAME
        )
    )
    op.alter_column("upload", "original_name", existing_type=sa.Text(), nullable=False)
