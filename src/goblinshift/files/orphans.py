"""Arquivos do storage que nenhuma linha viva de `stored_object` referencia.

O expurgo confirma o estado no banco antes de apagar o arquivo (`retention.py`); uma queda entre
os dois, ou um defeito, deixa arquivo no disco sem dono. Este relatório lê o disco e confere as
chaves no banco, em lotes, e separa três categorias que pedem decisões diferentes do
administrador:

- `sem-linha`: nenhuma linha referencia o arquivo, e o nome segue um formato que a aplicação
  cria (`uploads/<32 hex>` ou `runs/<uuid>/g<n>/<32 hex>`): órfão puro;
- `desconhecido`: nenhuma linha referencia o arquivo, mas o nome não é de nenhum formato da
  aplicação (um `dump.sql` ou uma nota deixada à mão na raiz). Nunca é apagado aqui;
- `marcado-removido-mas-presente`: a linha existe e está `deleted` ou `missing`, mas o arquivo
  continua no disco. Em `missing` o administrador pode preferir restaurar a linha a apagar o
  arquivo; por isso a categoria é separada.

Linha `pending`, `committed` ou `delete_requested` com arquivo não é órfã: o expurgo ainda a
trata. O relatório (`find_orphan_files`) não apaga nem altera arquivo ou linha, e cada lote roda
em transação `READ ONLY`.

Duas travas contra falso alarme. Arquivo modificado depois de `modified_before` fica de fora: é
gravação em curso (arquivo no disco antes de a linha ser confirmada). E o disco é lido antes do
banco em cada lote, de modo que arquivo criado depois da leitura nunca aparece como órfão. Sem a
sentinela de montagem a raiz pode ser só o disco do sistema, e "tudo órfão" seria falso:
`StorageNotMountedError`, sem consultar o banco.

`delete_orphan_files` apaga só `sem-linha` (o caso da restauração de backup: uploads e candidatos
criados depois do dump ficam no disco sem linha). Entre a lista e a remoção o arquivo pode ganhar
dono, por isso cada um é conferido de novo logo antes do `unlink`: qualquer linha com a chave, em
qualquer estado, ou um `mtime` que passou do corte, poupa o arquivo. Sobra uma janela entre essa
releitura e o `unlink` (milissegundos, sem trava: não há linha para travar): uma linha inserida
nela, para um arquivo que já existia antes do corte, não salva o arquivo. A aplicação não cria
linha nova para arquivo velho (a chave nasce aleatória com a linha), então só um operador
restaurando linhas à mão em paralelo cairia nela; não rode as duas coisas ao mesmo tempo.
"""

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from itertools import batched

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from goblinshift.files.states import ObjectState
from goblinshift.files.storage import (
    FileStorage,
    ObjectMissingError,
    ObjectTamperedError,
    StorageError,
    StoredFile,
)
from goblinshift.files.tables import stored_object

DEFAULT_BATCH_SIZE = 1000
_REMOVED_STATES = frozenset({ObjectState.DELETED, ObjectState.MISSING})
_HEX32 = "[0-9a-f]{32}"
_UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Os formatos que a aplicação grava: `files/uploads.py` (`uploads/<hex>`) e `files/artifacts.py`
# (`runs/<run_id>/g<geração>/<hex>`). Só eles podem ser apagados como órfãos.
_APPLICATION_KEY = re.compile(rf"uploads/{_HEX32}|runs/{_UUID}/g(?:0|[1-9][0-9]*)/{_HEX32}")


class OrphanReason(StrEnum):
    NO_ROW = "sem-linha"
    UNKNOWN = "desconhecido"
    MARKED_REMOVED = "marcado-removido-mas-presente"


class DeletionOutcome(StrEnum):
    """O que aconteceu com um `sem-linha` ao tentar apagá-lo."""

    DELETED = "apagado"
    CHANGED = "mudou-depois-da-lista"
    VANISHED = "sumiu"
    TAMPERED = "adulterado"
    FAILED = "falhou"


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
    unknown_count: int
    unknown_bytes: int
    listed: tuple[OrphanFile, ...]
    truncated: bool


@dataclass(frozen=True)
class OrphanDeletion:
    """Resultado por desfecho; `truncated` diz que sobrou `sem-linha` além de `limit`."""

    deleted: tuple[OrphanFile, ...]
    skipped: int
    vanished: tuple[str, ...]
    tampered: tuple[str, ...]
    failed: tuple[str, ...]
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
    totals = {reason: [0, 0] for reason in OrphanReason}
    listed: list[OrphanFile] = []
    count = 0
    for orphan in _orphans(session, storage, modified_before, batch_size):
        totals[orphan.reason][0] += 1
        totals[orphan.reason][1] += orphan.size_bytes
        count += 1
        if len(listed) < list_limit:
            listed.append(orphan)
    return OrphanReport(
        orphan_count=totals[OrphanReason.NO_ROW][0],
        orphan_bytes=totals[OrphanReason.NO_ROW][1],
        marked_removed_count=totals[OrphanReason.MARKED_REMOVED][0],
        marked_removed_bytes=totals[OrphanReason.MARKED_REMOVED][1],
        unknown_count=totals[OrphanReason.UNKNOWN][0],
        unknown_bytes=totals[OrphanReason.UNKNOWN][1],
        listed=tuple(listed),
        truncated=count > len(listed),
    )


def delete_orphan_files(
    session: Session,
    storage: FileStorage,
    *,
    modified_before: float,
    limit: int,
    on_outcome: Callable[[OrphanFile, DeletionOutcome], None] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> OrphanDeletion:
    """Apaga até `limit` arquivos `sem-linha` mais velhos que `modified_before` (epoch, em s).

    Percorre o disco como `find_orphan_files`; a releitura da linha e do `mtime` antes de cada
    `unlink` é o que separa "órfão na hora da lista" de "órfão agora". `on_outcome` recebe cada
    desfecho na hora em que acontece, para o rastro não depender de a função terminar. Falha ao
    apagar um arquivo conta em `failed` e não interrompe os outros.
    """
    found: dict[DeletionOutcome, list[OrphanFile]] = {outcome: [] for outcome in DeletionOutcome}
    truncated = False
    for orphan in _orphans(session, storage, modified_before, batch_size):
        if orphan.reason is not OrphanReason.NO_ROW:
            continue
        if len(found[DeletionOutcome.DELETED]) >= limit:
            truncated = True
            break
        outcome = _delete_if_still_orphan(session, storage, orphan.key, modified_before)
        found[outcome].append(orphan)
        if on_outcome is not None:
            on_outcome(orphan, outcome)
    return OrphanDeletion(
        deleted=tuple(found[DeletionOutcome.DELETED]),
        skipped=len(found[DeletionOutcome.CHANGED]),
        vanished=tuple(f.key for f in found[DeletionOutcome.VANISHED]),
        tampered=tuple(f.key for f in found[DeletionOutcome.TAMPERED]),
        failed=tuple(f.key for f in found[DeletionOutcome.FAILED]),
        truncated=truncated,
    )


def _orphans(
    session: Session, storage: FileStorage, modified_before: float, batch_size: int
) -> Iterator[OrphanFile]:
    """Os arquivos sem linha viva, mais velhos que o corte, lote a lote."""
    if not storage.is_mounted():
        raise StorageNotMountedError
    candidates = (f for f in storage.walk_objects() if f.modified_at < modified_before)
    for batch in batched(candidates, batch_size, strict=False):
        for file, reason in _classify(session, batch):
            yield OrphanFile(file.key, file.size_bytes, file.modified_at, reason)


def _classify(
    session: Session, batch: Iterable[StoredFile]
) -> Iterable[tuple[StoredFile, OrphanReason]]:
    files = list(batch)
    with session.begin():
        # O banco garante o "só leitura": qualquer escrita aqui falha com SQLSTATE 25006.
        session.execute(text("SET TRANSACTION READ ONLY"))
        rows = session.execute(
            select(stored_object.c.storage_key, stored_object.c.state).where(
                stored_object.c.storage_key.in_([f.key for f in files])
            )
        )
        states = {key: state for key, state in rows}
    for file in files:
        state = states.get(file.key)
        if state is None:
            known = _APPLICATION_KEY.fullmatch(file.key) is not None
            yield file, OrphanReason.NO_ROW if known else OrphanReason.UNKNOWN
        elif state in _REMOVED_STATES:
            yield file, OrphanReason.MARKED_REMOVED


def _delete_if_still_orphan(
    session: Session, storage: FileStorage, key: str, modified_before: float
) -> DeletionOutcome:
    with session.begin():
        row = session.execute(
            select(stored_object.c.id).where(stored_object.c.storage_key == key)
        ).first()
    if row is not None:
        return DeletionOutcome.CHANGED
    try:
        current = storage.stat(key)
    except ObjectMissingError:
        return DeletionOutcome.VANISHED
    except ObjectTamperedError:
        return DeletionOutcome.TAMPERED
    except OSError:
        return DeletionOutcome.FAILED
    if current.modified_at >= modified_before:
        return DeletionOutcome.CHANGED
    try:
        storage.delete(key)
    except OSError, StorageError:
        return DeletionOutcome.FAILED
    return DeletionOutcome.DELETED
