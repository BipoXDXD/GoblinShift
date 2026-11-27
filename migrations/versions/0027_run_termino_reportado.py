"""Marca de término reportado da Run: o `run.finished` sai ao menos uma vez (#442).

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-23 06:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só uma coluna anulável sem default, que
o código anterior ignora.

Quem conclui a Run emite o `run.finished` depois do commit terminal e então grava
`finished_reported_at`. O worker que cai entre o commit e a emissão deixava a Run sem evento, e o
SLO de execução a perdia; a manutenção agora reemite as Runs terminais sem a marca, passada a
carência.

Esta revisão só adiciona a coluna (Split Phase): o ACCESS EXCLUSIVE do ADD COLUMN dura só a
mudança no catálogo, sem reescrever nem varrer `run`, e cabe no `lock_timeout`; se não couber, o
retry do ADR 057 refaz a revisão do zero, porque nada dela foi gravado. A carga fica na 0028: o
backfill das Runs já terminais e o índice CONCURRENTLY da varredura, numa revisão que pode ser
repetida inteira. Juntos aqui, o commit do `autocommit_block` gravaria a coluna antes do carimbo,
e o retry falharia com DuplicateColumn; e o backfill rodaria sob o ACCESS EXCLUSIVE da coluna.

O downgrade remove a coluna, sem guarda de perda: a marca é derivada. Um ciclo de descer e subir
marca como reportadas as Runs que a varredura ainda não tinha reemitido.
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


def downgrade() -> None:
    op.drop_column("run", "finished_reported_at")
