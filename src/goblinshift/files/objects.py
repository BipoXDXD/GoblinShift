"""Usos ativos (`ObjectUse`) e exclusão adiada de `StoredObject` (docs/GoblinShiftPlan.md, 3.6).

`files` não conhece execuções nem downloads em andamento: quem usa um objeto registra o uso com
`hold` e o encerra com `release`, e a remoção física só considera esses usos. Assim excluir nunca
falha por "arquivo em uso"; o arquivo só sai do disco quando ninguém mais o usa.

As funções rodam na transação de quem chama.
"""

import uuid
from collections.abc import Collection

from sqlalchemy import ColumnElement, Select, and_, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from goblinshift.files.errors import ObjectNotAvailableError
from goblinshift.files.limits import USED_INPUT_TTL
from goblinshift.files.states import HolderKind, ObjectState
from goblinshift.files.tables import artifact, object_use, stored_object, upload


def hold(
    session: Session,
    object_ids: Collection[uuid.UUID],
    holder_kind: HolderKind,
    holder_id: uuid.UUID,
) -> None:
    """Registra o uso dos objetos por um detentor; repetir para o mesmo detentor é no-op.

    Trava os objetos com `FOR SHARE`, em ordem de ID, e exige que estejam `committed`: o expurgo
    trava com `FOR UPDATE SKIP LOCKED`, então nunca remove um objeto enquanto o uso é registrado.
    """
    ids = sorted(set(object_ids))
    if not ids:
        return
    available = session.execute(
        select(stored_object.c.id)
        .where(stored_object.c.id.in_(ids), stored_object.c.state == ObjectState.COMMITTED)
        .order_by(stored_object.c.id)
        .with_for_update(read=True)
    ).all()
    if len(available) != len(ids):
        raise ObjectNotAvailableError
    session.execute(
        insert(object_use)
        .values([{"object_id": i, "holder_kind": holder_kind, "holder_id": holder_id} for i in ids])
        .on_conflict_do_nothing(
            index_elements=["object_id", "holder_kind", "holder_id"],
            index_where=object_use.c.ended_at.is_(None),
        )
    )


def release(session: Session, holder_kind: HolderKind, holder_id: uuid.UUID) -> None:
    """Encerra todos os usos abertos do detentor; repetir é no-op.

    Para `HolderKind.RUN`, é aqui que a retenção de 48 h das entradas usadas passa a contar
    (1.7: "48 horas após o término"): os uploads prontos desses objetos ganham
    `expires_at = GREATEST(expires_at, now() + 48 h)`. `runs` deve chamar esta função ao
    terminar a execução, na mesma transação que a torna terminal. Um upload excluído fica de
    fora: a data dele já decidiu entre `deleted` e `expired`.
    """
    ended = (
        session.execute(
            update(object_use)
            .where(
                object_use.c.holder_kind == holder_kind,
                object_use.c.holder_id == holder_id,
                object_use.c.ended_at.is_(None),
            )
            .values(ended_at=func.now())
            .returning(object_use.c.object_id)
        )
        .scalars()
        .all()
    )
    if holder_kind == HolderKind.RUN and ended:
        session.execute(
            update(upload)
            .where(upload.c.object_id.in_(ended), upload.c.deleted_at.is_(None))
            .values(expires_at=func.greatest(upload.c.expires_at, func.now() + USED_INPUT_TTL))
        )


def request_deletion_of_unreferenced(session: Session, object_ids: Collection[uuid.UUID]) -> None:
    """Marca `delete_requested` os objetos que nenhum upload ou artefato válido referencia.

    O mesmo objeto pode ter duas referências (o Detox publica a entrada como saída), então
    invalidar uma delas não basta. Trava os objetos antes de conferir as referências: duas
    exclusões concorrentes do mesmo objeto se enfileiram, e a segunda enxerga a primeira já
    confirmada, em vez de cada uma ver a outra ainda válida (write skew).
    """
    ids = sorted(set(object_ids))
    if not ids:
        return
    session.execute(
        select(stored_object.c.id)
        .where(stored_object.c.id.in_(ids))
        .order_by(stored_object.c.id)
        .with_for_update()
    )
    session.execute(
        update(stored_object)
        .where(
            stored_object.c.id.in_(ids),
            stored_object.c.state == ObjectState.COMMITTED,
            ~_has_valid_reference(),
        )
        .values(state=ObjectState.DELETE_REQUESTED, delete_requested_at=func.now())
    )


def select_purgeable(limit: int) -> Select[tuple[uuid.UUID, str, int]]:
    """Objetos prontos para remoção física: pedidos, sem uso aberto e sem referência válida.

    Para a manutenção; trava com `SKIP LOCKED` para não esperar por quem registra um uso.
    """
    open_use = exists().where(
        object_use.c.object_id == stored_object.c.id, object_use.c.ended_at.is_(None)
    )
    return (
        select(stored_object.c.id, stored_object.c.storage_key, stored_object.c.size_bytes)
        .where(
            stored_object.c.state == ObjectState.DELETE_REQUESTED,
            ~open_use,
            ~_has_valid_reference(),
        )
        .order_by(stored_object.c.delete_requested_at, stored_object.c.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )


def _has_valid_reference() -> ColumnElement[bool]:
    valid_upload = exists().where(
        and_(upload.c.object_id == stored_object.c.id, upload.c.deleted_at.is_(None))
    )
    valid_artifact = exists().where(
        and_(artifact.c.object_id == stored_object.c.id, artifact.c.deleted_at.is_(None))
    )
    return valid_upload | valid_artifact
