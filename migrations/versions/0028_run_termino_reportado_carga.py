"""Backfill da marca de término reportado e índice parcial da varredura (#442).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-23 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só dados derivados na coluna da 0027 e
um índice parcial novo, que o código anterior ignora.

O backfill marca as Runs já terminais com o próprio `finished_at`, senão a primeira varredura
reemitiria todo o histórico dentro do horizonte. Como a coluna já existe (0027), o UPDATE pega só
ROW EXCLUSIVE em `run`, que não para leitura nem escrita das outras Runs, e lock de linha só nas
Runs terminais sem a marca, que quase ninguém mais escreve; nada de ACCESS EXCLUSIVE na tabela.
Roda na transação curta da revisão, e o `autocommit_block` o grava antes do índice.

O índice `run_finish_unreported_idx` cobre só as Runs terminais sem a marca, na ordem do
`finished_at`, que a varredura lê. Nasce `CONCURRENTLY`, fora da transação: `run` guarda o
histórico de 90 dias, e o `CREATE INDEX` comum travaria a escrita das Runs vivas durante a
construção.

A revisão pode ser repetida inteira, e o retry do `lock_timeout` (ADR 057) faz isso: o Alembic só
grava o carimbo da 0028 depois do `autocommit_block`, então o CREATE que desiste (55P03) faz a
revisão rodar de novo com o backfill já gravado. O UPDATE é idempotente (só toca Runs sem a
marca), e o `DROP ... IF EXISTS` apaga o índice INVALID que a tentativa anterior deixou antes de
criá-lo de novo. O carimbo da 0027 já saiu na transação dela, antes desta revisão começar.

Janela do deploy: uma Run terminada pelo código antigo depois do backfill fica sem a marca e sai
duas vezes (o código antigo já a emitiu, a varredura repete). O contrato do evento já admite
duplicata.

O downgrade remove só o índice. As marcas ficam: são derivadas, e o downgrade da 0027 leva a
coluna junto.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Backfill idempotente, sem ACCESS EXCLUSIVE: leitura e escrita das outras Runs seguem.
    # lock-ok: só ROW EXCLUSIVE em run e lock de linha nas Runs terminais sem a marca.
    op.execute(
        "UPDATE run SET finished_reported_at = finished_at "
        "WHERE finished_at IS NOT NULL AND finished_reported_at IS NULL"
    )
    with op.get_context().autocommit_block():
        # lock-ok: limpeza do índice INVALID de uma tentativa anterior, também concorrente.
        op.drop_index(
            "run_finish_unreported_idx",
            table_name="run",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            "run_finish_unreported_idx",
            "run",
            ["finished_at"],
            postgresql_concurrently=True,
            postgresql_where=sa.text("finished_at IS NOT NULL AND finished_reported_at IS NULL"),
        )


def downgrade() -> None:
    op.drop_index("run_finish_unreported_idx", table_name="run")
