"""Conta pendente explícita: `app_user.awaiting_approval`.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-14 12:00:00.000000

Antes, pendente (ADR 023) era "bloqueada e sem nenhuma `web_session`". A retenção apaga as
sessões 7 dias depois do fim, e a conta bloqueada por abuso voltava à fila de liberação e
logava como `PENDING_APPROVAL`. A marca passa a ser gravada no cadastro fechado e zerada no
bloqueio e na liberação.

Expand/contract: a coluna é `NOT NULL DEFAULT false` (no PG 11+ só muda o catálogo, sem
reescrever a tabela), e o código anterior nunca a lê. O backfill reproduz o critério antigo e
tira as contas com `ACCOUNT_BLOCKED` na auditoria: as bloqueadas por abuso cujas sessões já
saíram, até onde os 90 dias de auditoria alcançam. Nada a contrair.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("awaiting_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE app_user AS u
           SET awaiting_approval = true
         WHERE u.status = 'blocked'
           AND NOT EXISTS (SELECT 1 FROM web_session AS s WHERE s.user_id = u.id)
           AND NOT EXISTS (
                 SELECT 1 FROM audit_event AS e
                  WHERE e.user_id = u.id AND e.event_type = 'ACCOUNT_BLOCKED'
               )
        """
    )


def downgrade() -> None:
    op.drop_column("app_user", "awaiting_approval")
