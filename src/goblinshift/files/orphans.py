"""Arquivos do storage que nenhuma linha viva de `stored_object` referencia (somente leitura).

O expurgo confirma o estado no banco antes de apagar o arquivo (`retention.py`); uma queda entre
os dois, ou um defeito, deixa arquivo no disco sem dono. Este relatório lê o disco e confere as
chaves no banco, em lotes, e separa duas categorias que pedem decisões diferentes do
administrador:

- `sem-linha`: nenhuma linha referencia o arquivo (órfão puro);
- `marcado-removido-mas-presente`: a linha existe e está `deleted` ou `missing`, mas o arquivo
  continua no disco. Em `missing` o administrador pode preferir restaurar a linha a apagar o
  arquivo; por isso a categoria é separada.

Linha `pending`, `committed` ou `delete_requested` com arquivo não é órfã: o expurgo ainda a
trata. Nada aqui apaga ou altera arquivo ou linha.

Duas travas contra falso alarme. Arquivo modificado depois de `modified_before` fica de fora: é
gravação em curso (arquivo no disco antes de a linha ser confirmada). E o disco é lido antes do
banco em cada lote, de modo que arquivo criado depois da leitura nunca aparece como órfão. Sem a
sentinela de montagem a raiz pode ser só o disco do sistema, e "tudo órfão" seria falso:
`StorageNotMountedError`, sem consultar o banco.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from itertools import batched

from sqlalchemy import select
from sqlalchemy.orm import Session

from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage, StoredFile
from goblinshift.files.tables import stored_object

DEFAULT_BATCH_SIZE = 1000
_REMOVED_STATES = frozenset({ObjectState.DELETED, ObjectState.MISSING})


class OrphanReason(StrEnum):
    NO_ROW = "sem-linha"
    MARKED_REMOVED = "marcado-removido-mas-presente"


class StorageNotMountedError(Exception):
    def __init__(self) -> None:
        super().__init__("storage não montado; nada foi verificado")


@dataclass(frozen=True)
class OrphanFile:
    key: str
    size_bytes: int
    modified_at: float
    reason: OrphanReason


@dataclass(frozen=True)
class OrphanReport:
    """Totais completos por categoria; `listed` traz só os primeiros, até `list_limit`."""

    orphan_count: int
    orphan_bytes: int
    marked_removed_count: int
    marked_removed_bytes: int
    listed: tuple[OrphanFile, ...]
    truncated: bool


def find_orphan_files(
    session: Session,
    storage: FileStorage,
    *,
    modified_before: float,
    list_limit: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> OrphanReport:
    """Percorre o disco e classifica o que não tem linha viva; `modified_before` é epoch, em s.

    A sessão chega sem transação aberta: cada lote consulta o banco numa transação curta, e o
    percurso do disco corre entre elas, porque o banco derruba transação ociosa.
    """
    if not storage.is_mounted():
        raise StorageNotMountedError
    totals = {reason: [0, 0] for reason in OrphanReason}
    listed: list[OrphanFile] = []
    candidates = (f for f in storage.walk_objects() if f.modified_at < modified_before)
    for batch in batched(candidates, batch_size, strict=False):
        for file, reason in _classify(session, batch):
            totals[reason][0] += 1
            totals[reason][1] += file.size_bytes
            if len(listed) < list_limit:
                listed.append(OrphanFile(file.key, file.size_bytes, file.modified_at, reason))
    count = sum(count for count, _ in totals.values())
    return OrphanReport(
        orphan_count=totals[OrphanReason.NO_ROW][0],
        orphan_bytes=totals[OrphanReason.NO_ROW][1],
        marked_removed_count=totals[OrphanReason.MARKED_REMOVED][0],
        marked_removed_bytes=totals[OrphanReason.MARKED_REMOVED][1],
        listed=tuple(listed),
        truncated=count > len(listed),
    )


def _classify(
    session: Session, batch: Iterable[StoredFile]
) -> Iterable[tuple[StoredFile, OrphanReason]]:
    files = list(batch)
    with session.begin():
        rows = session.execute(
            select(stored_object.c.storage_key, stored_object.c.state).where(
                stored_object.c.storage_key.in_([f.key for f in files])
            )
        )
        states = {key: state for key, state in rows}
    for file in files:
        state = states.get(file.key)
        if state is None:
            yield file, OrphanReason.NO_ROW
        elif state in _REMOVED_STATES:
            yield file, OrphanReason.MARKED_REMOVED
