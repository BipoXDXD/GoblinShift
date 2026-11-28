"""Outbox de remoção de arquivos: `file_removal` (#442, 17b).

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-28 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Tabela nova, que o código anterior não lê
nem grava: a réplica anterior segue apagando o arquivo depois do commit sem registrar a chave, como
sempre fez, e o código novo passa a registrar.

A retenção confirma no banco antes de apagar do disco (`files/retention.py`). Até aqui, uma falha
ou queda entre o commit e o `unlink` deixava o arquivo no disco sem nada que repetisse a remoção.
Agora a chave entra em `file_removal` na mesma transação que tira o arquivo de uso; a remoção
depois do commit apaga o arquivo e, só então, a linha. A manutenção repete as linhas com mais de
um minuto (`retry_file_removals`) até o arquivo sair. A chave é a PK: pedir duas vezes a remoção
do mesmo arquivo é uma linha só. Sem índice em `requested_at`: a tabela só guarda o que ainda
não saiu do disco, e fica vazia no regime normal.

Os privilégios seguem os de `stored_object`: o `ALTER DEFAULT PRIVILEGES` do script de papéis
de runtime (ADR 055) já dá SELECT, INSERT, UPDATE e DELETE ao `gs_runtime` nas tabelas novas.

Travas (ADR 057): o CREATE TABLE não trava tabela com dados.

O downgrade apaga a tabela e, com ela, as remoções pendentes. Sem guarda de
`GS_ALLOW_LOSSY_DOWNGRADE`: nenhum dado de conta se perde, só a repetição; os arquivos ficam no
disco como órfãos, que `gs-admin orphan-files` lista.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_removal",
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("storage_key", name=op.f("file_removal_pkey")),
    )


def downgrade() -> None:
    op.drop_table("file_removal")
