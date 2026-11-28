"""Instante do pedido de parada da Run: a medição de `stopping` conta a partir dele (#442, 15a).

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-28 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só uma coluna anulável sem default, que
o código anterior ignora: o UPDATE dele não a cita e a deixa como está.

A Run que vai para `stopping` sem cancelamento (falha transitória que esgota as tentativas, com
`EXECUTOR_UNAVAILABLE`) tinha a idade medida por `least(cancel_requested_at, deadline_at)`. Com o
cancelamento nulo e o prazo ainda à frente, a manutenção só a contava como presa depois do prazo
mais os 10 min do alerta. A store passa a gravar `stop_requested_at` na transição para `stopping`,
e a medição lê `coalesce(stop_requested_at, least(cancel_requested_at, deadline_at))`: o
`coalesce` cobre a Run que o código anterior mandou para `stopping` durante o deploy.

O backfill dá às Runs já em `stopping` o mesmo valor que a medição usava, ou `now()` quando os dois
são nulos. Ele só olha as linhas ainda sem o instante, então rodar a 0032 de novo não muda nada.
Travas (ADR 057): o ADD COLUMN anulável e sem default só muda o catálogo; o UPDATE pega no máximo
as Runs em `stopping`, poucas porque cada uma segura a única vaga de tentativa física da conta, e
roda na transação do Alembic sob o `lock_timeout` de 2 s do `env.py`.

O downgrade remove a coluna, sem guarda de perda: o instante só serve à medição, e a Run volta a
ser medida pelo cancelamento ou pelo prazo, como antes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Só as linhas ainda sem o instante: repetir não muda as já preenchidas.
BACKFILL = """
UPDATE run
   SET stop_requested_at = COALESCE(LEAST(cancel_requested_at, deadline_at), now())
 WHERE status = 'stopping'
   AND stop_requested_at IS NULL
"""


def upgrade() -> None:
    op.add_column("run", sa.Column("stop_requested_at", sa.DateTime(timezone=True)))
    # lock-ok: só as poucas Runs em `stopping`, na transação curta da 0032.
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_column("run", "stop_requested_at")
