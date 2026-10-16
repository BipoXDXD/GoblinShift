"""Anulação de 30 dias (1.7, "Retenção"): nomes das saídas anuláveis.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-16 06:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Nada aqui é escrito pelo código atual:
`artifact.file_name` e `run_output.logical_name` só deixam de exigir valor, e o código anterior
continua gravando e lendo como antes. A anulação vem no deploy seguinte, pela manutenção.

O CHECK do artefato restringe o NULL a artefato excluído, como o `upload_name_until_deleted_check`
da 0010: o download lê o nome de artefato vivo para o `Content-Disposition`. O nome lógico não tem
CHECK: nada na linha de `run_output` diz se a Run terminou (um CHECK não lê outra tabela), e o único
leitor dele é a listagem de resultados, que troca NULL pelo nome genérico. A manutenção só anula os
dois juntos, e só para saída cujo artefato já foi excluído.

A UNIQUE de `(run_id, logical_name)` fica com NULLS DISTINCT de propósito: depois da anulação todas
as saídas da Run ficam sem nome, e NULL ali é ausência, não um nome que possa colidir.

O índice parcial cobre só as saídas que ainda têm nome, como pede 1.7. As tabelas guardam no
máximo 90 dias de histórico de uma VPS, então o `CREATE INDEX` comum, na transação, basta.

O downgrade devolve o NOT NULL preenchendo o que já foi anulado com o mesmo nome genérico que a
API usa (`<papel>-<id do artefato>`, ASCII e único por Run), porque o nome original não existe mais.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME_CHECK = "artifact_name_until_deleted_check"
_ANONYMIZE_INDEX = "run_output_anonymize_idx"


def upgrade() -> None:
    op.alter_column("artifact", "file_name", existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        op.f(_NAME_CHECK), "artifact", "file_name IS NOT NULL OR deleted_at IS NOT NULL"
    )
    op.alter_column("run_output", "logical_name", existing_type=sa.Text(), nullable=True)
    op.create_index(
        _ANONYMIZE_INDEX,
        "run_output",
        ["run_id"],
        postgresql_where=sa.text("logical_name IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_ANONYMIZE_INDEX, table_name="run_output")
    op.execute(
        "UPDATE run_output SET logical_name = artifact.role || '-' || artifact.id::text "
        "FROM artifact WHERE artifact.id = run_output.artifact_id "
        "AND run_output.logical_name IS NULL"
    )
    op.alter_column("run_output", "logical_name", existing_type=sa.Text(), nullable=False)
    op.drop_constraint(op.f(_NAME_CHECK), "artifact", type_="check")
    op.execute("UPDATE artifact SET file_name = role || '-' || id::text WHERE file_name IS NULL")
    op.alter_column("artifact", "file_name", existing_type=sa.Text(), nullable=False)
