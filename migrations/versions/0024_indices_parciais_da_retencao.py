"""Índices parciais das buscas da retenção de arquivos (#442, 23g, 23h e item 12).

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-21 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só índices novos, que o código anterior
ignora. Ao contrário da 0019, nascem `CONCURRENTLY`, fora da transação: as três tabelas guardam 90
dias de linhas, e a criação não pode travar a escrita da API nem do worker. Um retry depois de
timeout do `CONCURRENTLY` acha o índice inválido e o refaz (`DROP INDEX CONCURRENTLY IF EXISTS`,
ADR 057).

- `object_use_ended_at_idx`: os usos encerrados há 90 dias que `delete_old_file_rows` apaga. O
  parcial deixa de fora os usos abertos, que nunca entram nessa busca.
- `stored_object_gone_idx`: os objetos `deleted`/`missing` que a mesma purga apaga em ordem de
  `id`; os vivos, a maioria, ficam de fora.
- `upload_incomplete_idx`: os uploads incompletos que `expire_incomplete_uploads` busca em ordem
  de `(expires_at, id)`. O `upload_expires_at_idx` não serve: o `OR deleted_at` da busca não prova
  o predicado `deleted_at IS NULL` dele.

O código filtra `state` com literal no SQL, nunca parâmetro: o plano genérico, que o psycopg passa
a usar depois de 5 execuções preparadas, só prova o predicado parcial com o valor à vista.

O downgrade só remove os índices, sem perder dado.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS object_use_ended_at_idx")  # lock-ok: limpeza
        op.create_index(
            "object_use_ended_at_idx",
            "object_use",
            ["ended_at"],
            postgresql_concurrently=True,
            postgresql_where=sa.text("ended_at IS NOT NULL"),
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS stored_object_gone_idx")  # lock-ok: limpeza
        op.create_index(
            "stored_object_gone_idx",
            "stored_object",
            ["id"],
            postgresql_concurrently=True,
            postgresql_where=sa.text("state IN ('deleted', 'missing')"),
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS upload_incomplete_idx")  # lock-ok: limpeza
        op.create_index(
            "upload_incomplete_idx",
            "upload",
            ["expires_at", "id"],
            postgresql_concurrently=True,
            postgresql_where=sa.text("state <> 'ready'"),
        )


def downgrade() -> None:
    op.drop_index("upload_incomplete_idx", table_name="upload")
    op.drop_index("stored_object_gone_idx", table_name="stored_object")
    op.drop_index("object_use_ended_at_idx", table_name="object_use")
