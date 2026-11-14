"""Índices das buscas da manutenção e da FK de `object_use` (#442).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-14 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só índices novos, que o código anterior
ignora. As tabelas são pequenas (uma VPS, poucas contas), então o `CREATE INDEX` comum, dentro da
transação do Alembic, basta, como na 0010.

- `object_use(object_id)`, completo: o parcial `object_use_open_idx` só cobre os usos abertos e
  não serve à checagem RESTRICT da FK ao apagar um `stored_object`, nem ao "nenhum uso" da
  retenção dos arquivos.
- `stored_object(attempt_id)`, parcial: os candidatos de uma tentativa (descarte e retenção das
  Runs); objetos sem tentativa nunca são buscados por ela.
- `least(revoked_at, idle_expires_at, absolute_expires_at)` de `web_session`: a expressão idêntica
  ao predicado da limpeza de sessões, senão o planejador não a casa com o índice.
- `resource_reservation(released_at)`, parcial: a limpeza das reservas liberadas; as vivas, com
  `released_at` nulo, nunca entram nela.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("object_use_object_id_idx", "object_use", ["object_id"])
    op.create_index(
        "stored_object_attempt_id_idx",
        "stored_object",
        ["attempt_id"],
        postgresql_where=sa.text("attempt_id IS NOT NULL"),
    )
    op.create_index(
        "web_session_ended_at_idx",
        "web_session",
        [sa.text("least(revoked_at, idle_expires_at, absolute_expires_at)")],
    )
    op.create_index(
        "resource_reservation_released_at_idx",
        "resource_reservation",
        ["released_at"],
        postgresql_where=sa.text("released_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("resource_reservation_released_at_idx", table_name="resource_reservation")
    op.drop_index("web_session_ended_at_idx", table_name="web_session")
    op.drop_index("stored_object_attempt_id_idx", table_name="stored_object")
    op.drop_index("object_use_object_id_idx", table_name="object_use")
