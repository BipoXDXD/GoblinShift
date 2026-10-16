"""Objetos que sumiram do storage viram `missing` (docs/GoblinShiftPlan.md, 1.7 e 3.8).

O storage não tem backup (ADR 009): depois de restaurar o banco de um dump anterior, ou de um
defeito no disco, linhas `committed` podem apontar para arquivos que já não existem. Elas são
marcadas `missing`, nunca substituídas por arquivos vazios, e o upload ou artefato passa a ter
disponibilidade `missing` (respondendo 410 no download).

Só `committed` vira `missing`: um objeto com exclusão pedida segue pelo expurgo, que já tolera o
arquivo ausente, e um pendente ainda não tem conteúdo. O arquivo de um objeto confirmado nunca
some por caminho normal, porque o expurgo troca o estado antes de apagar.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from goblinshift import capacity
from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage, ObjectMissingError
from goblinshift.files.tables import stored_object


@dataclass(frozen=True)
class MissingSweep:
    checked: int
    marked: int
    # Último ID do lote, se ele veio cheio; `None` quando a varredura chegou ao fim e recomeça.
    resume_after: uuid.UUID | None
    # Havia objetos a conferir, mas a raiz do storage estava vazia ou ausente: nada foi marcado.
    storage_unreachable: bool


def mark_missing(session: Session, object_ids: Collection[uuid.UUID]) -> int:
    """`committed` → `missing` e libera a reserva do objeto; devolve quantos mudaram.

    Na transação de quem chama. Repetir é no-op, e um objeto que entrementes foi pedido para
    exclusão ou removido fica como está: a mudança é condicional ao estado `committed`. A
    reserva sai porque o arquivo já não ocupa disco e o expurgo, que a liberaria, nunca vai
    tocar um objeto `missing`. Ordem de locks de 3.4: objetos em ordem de ID, pools por último.
    """
    ids = sorted(set(object_ids))
    if not ids:
        return 0
    locked = (
        select(stored_object.c.id)
        .where(stored_object.c.id.in_(ids), stored_object.c.state == ObjectState.COMMITTED)
        .order_by(stored_object.c.id)
        .with_for_update()
    )
    marked = list(
        session.execute(
            update(stored_object)
            .where(stored_object.c.id.in_(locked.scalar_subquery()))
            .values(state=ObjectState.MISSING)
            .returning(stored_object.c.id)
        ).scalars()
    )
    capacity.release_many(session, holder=capacity.HolderKind.OBJECT, holder_ids=marked)
    return len(marked)


def sweep_missing_objects(
    session: Session, storage: FileStorage, *, after: uuid.UUID | None, limit: int
) -> MissingSweep:
    """Confere um lote de objetos `committed`, em ordem de ID a partir de `after`.

    A leitura e a marcação são transações curtas separadas; o `stat` no disco fica entre elas,
    fora de transação. Quem chama guarda `resume_after` para o próximo lote, de modo que cada
    ciclo da manutenção custa no máximo `limit` consultas ao disco.
    """
    with session.begin():
        query = select(stored_object.c.id, stored_object.c.storage_key).where(
            stored_object.c.state == ObjectState.COMMITTED
        )
        if after is not None:
            query = query.where(stored_object.c.id > after)
        rows = session.execute(query.order_by(stored_object.c.id).limit(limit)).all()
    if not rows:
        return MissingSweep(checked=0, marked=0, resume_after=None, storage_unreachable=False)
    if not storage.has_content():
        return MissingSweep(checked=0, marked=0, resume_after=after, storage_unreachable=True)
    absent = [row.id for row in rows if not _exists(storage, row.storage_key)]
    marked = 0
    if absent:
        with session.begin():
            marked = mark_missing(session, absent)
    return MissingSweep(
        checked=len(rows),
        marked=marked,
        resume_after=rows[-1].id if len(rows) == limit else None,
        storage_unreachable=False,
    )


def _exists(storage: FileStorage, key: str) -> bool:
    try:
        storage.stat(key)
    except ObjectMissingError:
        return False
    return True
