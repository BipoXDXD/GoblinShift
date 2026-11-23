"""Marca de término reportado da Run: o `run.finished` sai ao menos uma vez (#442).

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-23 06:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só uma coluna anulável sem default e um
índice parcial novos, que o código anterior ignora.

Quem conclui a Run emite o `run.finished` depois do commit terminal e então grava
`finished_reported_at`. O worker que cai entre o commit e a emissão deixava a Run sem evento, e o
SLO de execução a perdia; a manutenção agora reemite as Runs terminais sem a marca, passada a
carência. O backfill marca as Runs já terminais com o próprio `finished_at`, senão a primeira
varredura reemitiria todo o histórico dentro do horizonte. Ele só toca Runs terminais, que quase
ninguém mais escreve, e é idempotente.

Janela do deploy: uma Run terminada pelo código antigo depois do backfill fica sem a marca e sai
duas vezes (o código antigo já a emitiu, a varredura repete). O contrato do evento já admite
duplicata.

O índice `run_finish_unreported_idx` cobre só as Runs terminais sem a marca, na ordem do
`finished_at`, que a varredura lê. Ao contrário da 0019 e da 0026, nasce `CONCURRENTLY`, fora da
transação: `run` guarda o histórico de 90 dias, e o `CREATE INDEX` comum travaria a escrita das
Runs vivas durante a construção. O `DROP ... IF EXISTS` antes deixa o retry do `lock_timeout`
(ADR 057) refazer um índice que ficou INVALID.

O downgrade remove o índice e a coluna, sem guarda de perda: a marca é derivada. Um ciclo de
descer e subir marca como reportadas as Runs que a varredura ainda não tinha reemitido.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run", sa.Column("finished_reported_at", sa.DateTime(timezone=True)))
    # lock-ok: só Runs terminais (ROW EXCLUSIVE, não bloqueia leitura), que quase ninguém escreve.
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
    op.drop_column("run", "finished_reported_at")
