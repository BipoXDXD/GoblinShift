"""Chave de idempotência do lote de upload: `request_key`, `request_hash` e `request_index` (#442).

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-22 12:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Colunas anuláveis e sem default: o
código anterior não as lê nem grava, e `ADD COLUMN` sem default não reescreve a tabela. O lote
criado com `Idempotency-Key` grava as três em cada linha; sem a chave, as três ficam nulas.

O número pula de 0019 para 0025 porque o rascunho #503 reserva 0020 a 0024. Com as duas linhas
juntas, esta revisão segue a 0024: a cadeia fica 0020 a 0024 e depois 0025.

Os CHECKs e os índices parciais entram na transação do Alembic, como na 0006 e na 0019: as colunas
acabaram de nascer nulas em todas as linhas, e `upload` só guarda os uploads da retenção. O
segundo índice, por `created_at` nas linhas com chave, guia a manutenção que anula a chave depois
de 24 h (`files.retention.forget_upload_request_keys`) sem varrer a tabela inteira.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("upload", sa.Column("request_key", sa.Text(), nullable=True))
    op.add_column("upload", sa.Column("request_hash", sa.LargeBinary(), nullable=True))
    op.add_column("upload", sa.Column("request_index", sa.SmallInteger(), nullable=True))
    # lock-ok: colunas recém-criadas, nulas em todas as linhas; varredura curta de upload
    op.create_check_constraint(
        op.f("upload_request_key_check"), "upload", "request_key ~ '^[A-Za-z0-9_-]{16,64}$'"
    )
    # lock-ok: idem, coluna recém-criada e nula
    op.create_check_constraint(
        op.f("upload_request_hash_check"), "upload", "octet_length(request_hash) = 32"
    )
    # lock-ok: idem, colunas recém-criadas e nulas
    op.create_check_constraint(
        op.f("upload_request_check"),
        "upload",
        "(request_key IS NULL) = (request_hash IS NULL)"
        " AND (request_key IS NULL) = (request_index IS NULL)",
    )
    # lock-ok: índice parcial sobre coluna nula recém-criada; nenhuma linha entra nele
    op.create_index(
        op.f("upload_owner_id_request_key_idx"),
        "upload",
        ["owner_id", "request_key"],
        postgresql_where=sa.text("request_key IS NOT NULL"),
    )
    # lock-ok: idem, índice parcial sobre coluna nula recém-criada
    op.create_index(
        "upload_request_created_at_idx",
        "upload",
        ["created_at"],
        postgresql_where=sa.text("request_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("upload_request_created_at_idx", table_name="upload")
    op.drop_index(op.f("upload_owner_id_request_key_idx"), table_name="upload")
    op.drop_constraint(op.f("upload_request_check"), "upload", type_="check")
    op.drop_constraint(op.f("upload_request_hash_check"), "upload", type_="check")
    op.drop_constraint(op.f("upload_request_key_check"), "upload", type_="check")
    op.drop_column("upload", "request_index")
    op.drop_column("upload", "request_hash")
    op.drop_column("upload", "request_key")
