"""Papel de artefato `preview` e `artifact.source_artifact_id` (pré-visualização, 2.2 e 3.6).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-16 04:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Alarga o CHECK de `artifact.role` e
acrescenta a coluna anulável que liga o preview à saída de que ele deriva, com FK composta ao
dono (o banco impede apontar para artefato de outra conta), em cascata: o preview não existe sem
a saída de que deriva, e a purga apaga artefatos por objeto, em qualquer ordem. As linhas
existentes não são previews e ficam com a coluna nula, o que o CHECK novo exige. A tabela de
artefatos guarda só 48 h de saídas, então criar índice e constraints na transação, sem
`CONCURRENTLY`, basta.

O downgrade apaga os previews e só roda com `GS_ALLOW_LOSSY_DOWNGRADE=1` se houver linhas: o
CHECK antigo não os aceita. Antes, marca `delete_requested` os objetos que só eles apontavam,
para a purga da manutenção removê-los do disco.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_CHECK = "artifact_role_check"
_SOURCE_CHECK = "artifact_preview_source_check"
_SOURCE_FK = "artifact_owner_id_source_artifact_id_fkey"
_SOURCE_INDEX = "artifact_source_artifact_id_idx"


def upgrade() -> None:
    # lock-ok: DROP CONSTRAINT de CHECK só muda o catálogo: lock de um instante, sem varredura
    op.drop_constraint(op.f(_ROLE_CHECK), "artifact", type_="check")
    # lock-ok: alarga o CHECK de role; artifact guarda só 48 h de saídas, varre em ms
    op.create_check_constraint(
        op.f(_ROLE_CHECK),
        "artifact",
        "role IN ('output', 'report', 'manifest', 'package', 'preview')",
    )
    op.add_column("artifact", sa.Column("source_artifact_id", sa.Uuid(), nullable=True))
    # lock-ok: FK sobre source_artifact_id recém-criada e nula: validação imediata
    op.create_foreign_key(
        _SOURCE_FK,
        "artifact",
        "artifact",
        ["owner_id", "source_artifact_id"],
        ["owner_id", "id"],
        ondelete="CASCADE",
    )
    # lock-ok: coluna nula recém-criada, nenhuma linha é preview; artifact guarda 48 h
    op.create_check_constraint(
        op.f(_SOURCE_CHECK),
        "artifact",
        "(role = 'preview') = (source_artifact_id IS NOT NULL)",
    )
    # lock-ok: índice parcial em artifact, que guarda só 48 h de saídas
    op.create_index(
        _SOURCE_INDEX,
        "artifact",
        ["source_artifact_id"],
        unique=False,
        postgresql_where=sa.text("source_artifact_id IS NOT NULL"),
    )


def downgrade() -> None:
    if os.environ.get("GS_ALLOW_LOSSY_DOWNGRADE") != "1":
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT FROM artifact WHERE role = 'preview') "
            "THEN RAISE EXCEPTION 'o downgrade da 0014 apagaria os previews; "
            "GS_ALLOW_LOSSY_DOWNGRADE=1 aceita a perda'; END IF; END $$"
        )
    # Antes de apagar os previews, pede a remoção dos objetos que só eles apontam, como
    # `files.objects.request_deletion_of_unreferenced`: sem isso o arquivo ficaria `committed` e
    # sem referência, fora do alcance da purga e ocupando a cota. O objeto que outro artefato ou
    # upload ainda válido aponta fica como está.
    op.execute(
        """
        UPDATE stored_object SET state = 'delete_requested', delete_requested_at = now()
        WHERE state = 'committed'
          AND id IN (SELECT object_id FROM artifact WHERE role = 'preview')
          AND NOT EXISTS (
              SELECT 1 FROM artifact
              WHERE artifact.object_id = stored_object.id
                AND artifact.role <> 'preview'
                AND artifact.deleted_at IS NULL
          )
          AND NOT EXISTS (
              SELECT 1 FROM upload
              WHERE upload.object_id = stored_object.id AND upload.deleted_at IS NULL
          )
        """
    )
    op.execute(
        "DELETE FROM run_output WHERE artifact_id IN "
        "(SELECT id FROM artifact WHERE role = 'preview')"
    )
    op.execute("DELETE FROM artifact WHERE role = 'preview'")
    op.drop_index(_SOURCE_INDEX, table_name="artifact")
    op.drop_constraint(op.f(_SOURCE_CHECK), "artifact", type_="check")
    op.drop_constraint(_SOURCE_FK, "artifact", type_="foreignkey")
    op.drop_column("artifact", "source_artifact_id")
    op.drop_constraint(op.f(_ROLE_CHECK), "artifact", type_="check")
    op.create_check_constraint(
        op.f(_ROLE_CHECK), "artifact", "role IN ('output', 'report', 'manifest', 'package')"
    )
