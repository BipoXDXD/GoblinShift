"""Purga em lotes dos arquivos de uma conta com exclusão pedida (docs/GoblinShiftPlan.md, 1.8).

Pede a exclusão dos objetos e, quando nenhum sobrar, apaga as linhas de `upload`, `artifact` e
`stored_object`. A remoção física segue o caminho normal, que espera os usos abertos (downloads
de até 10 min) e libera as reservas de capacidade.

Roda depois da purga de `runs`: `run_input` e `run_output` apontam para uploads e artefatos com
FK RESTRICT. Cada chamada é um lote na transação de quem chama, e repetir qualquer lote é seguro:
a purga interrompida retoma do estado gravado no banco.
"""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import Table, delete, exists, func, select, update
from sqlalchemy.orm import Session

from goblinshift.files.objects import has_valid_reference, request_deletion_of_unreferenced
from goblinshift.files.states import ObjectState
from goblinshift.files.tables import artifact, object_use, stored_object, transfer_slot, upload

# Objetos que ainda têm arquivo no disco ou um escritor em andamento: a retenção cuida deles.
_LIVE_OBJECT_STATES = (ObjectState.PENDING, ObjectState.COMMITTED, ObjectState.DELETE_REQUESTED)


def purge_owner_batch(session: Session, owner_id: UUID, limit: int) -> bool:
    """Processa até `limit` itens; True quando não sobra nada da conta em `files`."""
    if _request_deletion_batch(session, owner_id, limit):
        return False
    if _has_live_objects(session, owner_id):
        return False
    _delete_rows_batch(session, owner_id, limit)
    return not _has_rows(session, owner_id)


def attempts_with_pending_outputs(session: Session, attempt_ids: Collection[UUID]) -> set[UUID]:
    """Tentativas que ainda têm saída pendente (objeto sem conteúdo confirmado).

    A purga de `runs` espera o descarte normal dessas saídas antes de apagar a tentativa.
    """
    if not attempt_ids:
        return set()
    return set(
        session.execute(
            select(stored_object.c.attempt_id)
            .where(
                stored_object.c.attempt_id.in_(attempt_ids),
                stored_object.c.state == ObjectState.PENDING,
            )
            .distinct()
        ).scalars()
    )


def _request_deletion_batch(session: Session, owner_id: UUID, limit: int) -> bool:
    """Exclui uploads e artefatos e pede a exclusão dos objetos sem referência; True se marcou.

    Upload incompleto excluído sai com o próprio objeto pendente em `expire_incomplete_uploads`.
    """
    upload_objects = _mark_deleted(session, upload, owner_id, limit)
    artifact_objects = _mark_deleted(session, artifact, owner_id, limit)
    # Candidatos confirmados que não viraram artefato não têm upload nem artefato a excluir.
    unreferenced = list(
        session.execute(
            select(stored_object.c.id)
            .where(
                stored_object.c.owner_id == owner_id,
                stored_object.c.state == ObjectState.COMMITTED,
                ~has_valid_reference(),
            )
            .limit(limit)
        ).scalars()
    )
    request_deletion_of_unreferenced(session, [*upload_objects, *artifact_objects, *unreferenced])
    return bool(upload_objects or artifact_objects or unreferenced)


def _mark_deleted(session: Session, table: Table, owner_id: UUID, limit: int) -> list[UUID]:
    """`deleted_at` nos registros ainda válidos da conta; devolve os objetos deles."""
    chosen = (
        select(table.c.id)
        .where(table.c.owner_id == owner_id, table.c.deleted_at.is_(None))
        .order_by(table.c.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    return list(
        session.execute(
            update(table)
            .where(table.c.id.in_(chosen))
            .values(deleted_at=func.now())
            .returning(table.c.object_id)
        ).scalars()
    )


def _has_live_objects(session: Session, owner_id: UUID) -> bool:
    live = exists().where(
        stored_object.c.owner_id == owner_id, stored_object.c.state.in_(_LIVE_OBJECT_STATES)
    )
    return bool(session.execute(select(live)).scalar_one())


def _delete_rows_batch(session: Session, owner_id: UUID, limit: int) -> None:
    """Apaga as linhas de objetos já removidos do disco, com o que aponta para eles."""
    objects = list(
        session.execute(
            select(stored_object.c.id)
            .where(stored_object.c.owner_id == owner_id)
            .order_by(stored_object.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    if objects:
        session.execute(delete(object_use).where(object_use.c.object_id.in_(objects)))
        session.execute(delete(upload).where(upload.c.object_id.in_(objects)))
        session.execute(delete(artifact).where(artifact.c.object_id.in_(objects)))
        session.execute(delete(stored_object).where(stored_object.c.id.in_(objects)))
    # Vagas são poucas (2 por direção) e, sem objeto vivo, não há transferência em andamento.
    session.execute(delete(transfer_slot).where(transfer_slot.c.owner_id == owner_id))


def _has_rows(session: Session, owner_id: UUID) -> bool:
    left = exists().where(stored_object.c.owner_id == owner_id) | exists().where(
        transfer_slot.c.owner_id == owner_id
    )
    return bool(session.execute(select(left)).scalar_one())
