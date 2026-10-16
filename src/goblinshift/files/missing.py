"""Objetos que sumiram do storage viram `missing` (docs/GoblinShiftPlan.md, 1.7 e 3.8).

O storage não tem backup (ADR 009): depois de restaurar o banco de um dump anterior, ou de um
defeito no disco, linhas `committed` podem apontar para arquivos que já não existem. Elas são
marcadas `missing`, nunca substituídas por arquivos vazios, e o upload ou artefato passa a ter
disponibilidade `missing` (respondendo 410 no download).

Só `committed` vira `missing`: um objeto com exclusão pedida segue pelo expurgo, que já tolera o
arquivo ausente, e um pendente ainda não tem conteúdo. O arquivo de um objeto confirmado nunca
some por caminho normal, porque o expurgo troca o estado antes de apagar.

`missing` é final, e a reserva sai na marcação. Sem backup de arquivos, nada no serviço devolve
um arquivo perdido; o risco é marcar por engano, e é ele que as travas abaixo cobrem. Por isso só
a varredura da manutenção marca, nunca uma requisição de usuário, e ela só marca com a sentinela
de montagem presente (`FileStorage.is_mounted`) e com no máximo `max_absent_ratio` do lote
ausente (num lote pequeno, sem todos ausentes): muito arquivo sumido de uma vez indica storage
errado, não arquivo perdido, e quem decide é o administrador. Erro de disco que não seja ausência
deixa o objeto como está.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum, auto

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from goblinshift import capacity
from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage, ObjectMissingError
from goblinshift.files.tables import stored_object

# Mais da metade do lote ausente de uma vez: storage errado, até o administrador dizer o contrário.
DEFAULT_MAX_ABSENT_RATIO = 0.5
# Abaixo disto o lote é pequeno demais para a fração dizer algo sobre o storage.
GOVERNOR_MIN_SAMPLE = 10
# Num lote pequeno, todos ausentes a partir deste tamanho também acionam o limitador.
_SMALL_LOT_MIN_ALL_ABSENT = 2


@dataclass(frozen=True)
class MissingSweep:
    checked: int
    absent: int
    # O `stat` falhou por outro motivo (EIO, permissão): nem ausente nem marcado; o próximo volta.
    unreadable: int
    marked: int
    # Último ID do lote, se ele veio cheio; `None` quando a varredura chegou ao fim e recomeça.
    resume_after: uuid.UUID | None
    # Havia objetos a conferir, mas a sentinela de montagem não estava lá: nada foi conferido.
    storage_unreachable: bool
    # Ausentes demais no lote: nada foi marcado, e o mesmo lote volta no ciclo seguinte.
    governor_tripped: bool

    @classmethod
    def idle(
        cls, *, resume_after: uuid.UUID | None = None, storage_unreachable: bool = False
    ) -> MissingSweep:
        """Nada conferido: sem objetos, sem a sentinela ou com a varredura falhando."""
        return cls(
            checked=0,
            absent=0,
            unreadable=0,
            marked=0,
            resume_after=resume_after,
            storage_unreachable=storage_unreachable,
            governor_tripped=False,
        )


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
    session: Session,
    storage: FileStorage,
    *,
    after: uuid.UUID | None,
    limit: int,
    max_absent_ratio: float,
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
        return MissingSweep.idle()
    if not storage.is_mounted():
        return MissingSweep.idle(resume_after=after, storage_unreachable=True)
    presence = {row.id: _presence(storage, row.storage_key) for row in rows}
    absent = [object_id for object_id, found in presence.items() if found is _Presence.ABSENT]
    unreadable = sum(found is _Presence.UNREADABLE for found in presence.values())
    if _too_many_absent(len(absent), unreadable, len(rows), max_absent_ratio):
        return MissingSweep(
            checked=len(rows),
            absent=len(absent),
            unreadable=unreadable,
            marked=0,
            resume_after=after,
            storage_unreachable=False,
            governor_tripped=True,
        )
    marked = 0
    if absent:
        with session.begin():
            marked = mark_missing(session, absent)
    return MissingSweep(
        checked=len(rows),
        absent=len(absent),
        unreadable=unreadable,
        marked=marked,
        resume_after=rows[-1].id if len(rows) == limit else None,
        storage_unreachable=False,
        governor_tripped=False,
    )


def _too_many_absent(absent: int, unreadable: int, checked: int, max_absent_ratio: float) -> bool:
    """Ausentes demais para ser perda de arquivo, e não storage errado.

    Ilegível junto com ausente aciona sempre: disco devolvendo EIO é storage com problema, e o
    ausente ao lado dele não prova perda. Daí em diante todo conferido é legível, e os ilegíveis
    não têm como diluir a fração. Num lote pequeno (o fim de cada volta, ou um ambiente com poucos
    objetos), a fração não diz muito; ali o sinal é todos sumirem de uma vez, a partir de dois.
    """
    if absent == 0:
        return False
    if unreadable > 0:
        return True
    if checked < GOVERNOR_MIN_SAMPLE:
        return checked >= _SMALL_LOT_MIN_ALL_ABSENT and absent == checked
    return absent > max_absent_ratio * checked


class _Presence(Enum):
    PRESENT = auto()
    ABSENT = auto()
    UNREADABLE = auto()


def _presence(storage: FileStorage, key: str) -> _Presence:
    try:
        storage.stat(key)
    except ObjectMissingError:
        return _Presence.ABSENT
    except OSError:
        # Erro de disco não prova ausência: o objeto fica como está, e a varredura segue.
        return _Presence.UNREADABLE
    return _Presence.PRESENT
