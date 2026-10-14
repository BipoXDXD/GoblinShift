"""Reconcilia `artifact.file_name` em bancos criados por uma versão anterior da 0002.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14 05:30:00.000000

A 0002 ganhou `file_name` (e o CHECK de tamanho) depois de já ter sido aplicada na VPS. O
Alembic não reaplica uma revisão registrada, então a produção ficou sem a coluna. Num banco
criado pela 0002 atual, esta migração não faz nada. Migração aplicada nunca é editada: a
correção de um schema já implantado vem sempre numa revisão nova.

A coluna nasce NOT NULL sem default de propósito: se houver artefatos sem nome, a migração
falha em vez de inventar um.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE artifact ADD COLUMN IF NOT EXISTS file_name text NOT NULL")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'artifact_file_name_check'
                  AND conrelid = 'artifact'::regclass
            ) THEN
                ALTER TABLE artifact ADD CONSTRAINT artifact_file_name_check
                    CHECK (octet_length(file_name) BETWEEN 1 AND 240);
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    # A coluna pertence à 0002; voltar para a 0003 não a remove.
    pass
