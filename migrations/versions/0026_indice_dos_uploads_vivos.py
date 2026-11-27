"""Índice dos uploads vivos da conta: `upload(owner_id, expires_at)` sem exclusão (#442).

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-22 18:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só um índice novo, que o código anterior
ignora.

Cada lote novo conta os uploads vivos da conta (teto de `files.uploads`). O índice
`(owner_id, created_at)` acharia a conta, mas varreria também as linhas excluídas, que ficam 90
dias para o histórico. Este é parcial nas linhas sem exclusão e ordenado pelo vencimento: a
contagem lê só as linhas da conta ainda não vencidas, que o próprio teto limita, e a espera do
`Retry-After` sai na ordem do índice. O predicado não tem parâmetro, então casa também com plano
genérico de prepared statement.

O `CREATE INDEX` comum, na transação do Alembic, basta, como na 0019 e na 0025: `upload` só
guarda os uploads da retenção.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # lock-ok: índice parcial em `upload`, que só guarda os uploads da retenção.
    op.create_index(
        op.f("upload_owner_id_expires_at_idx"),
        "upload",
        ["owner_id", "expires_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(op.f("upload_owner_id_expires_at_idx"), table_name="upload")
