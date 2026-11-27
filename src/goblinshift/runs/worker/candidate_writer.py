"""Saídas coletadas viram objetos candidatos (docs/GoblinShiftPlan.md, 3.4; ADRs 019 e 020).

Cada saída ganha um objeto `pending` em `runs/{run}/g{geração}/…`, numa transação curta; o
conteúdo é copiado de `out/` para o storage fora de transação e confirmado (`committed`) noutra
transação curta. Saída de conteúdo idêntica a uma entrada da Run, pelo SHA-256 e tamanho que a
coleta calculou, não é gravada de novo: o artefato aponta para o objeto da entrada enquanto ele
estiver `committed` (ADR 020; 1.4, "renomear não duplica objetos").

A cópia reabre cada saída como a coleta a leu (`open_collected`: sem seguir link, com o inode da
varredura) e confere o SHA-256 e o tamanho gravados contra os coletados. O sandbox pode ter
reescrito a saída depois da coleta, até no mesmo inode: divergência levanta `CollectionError`, e o
objeto publicado sempre tem o hash que a coleta conferiu contra o manifesto e o pacote.

Quem chama decide o fencing e o lease: `lock_running` abre cada transação travando a Run, e
`beating` envolve a cópia para renovar o lease entre blocos. O que eles levantarem (fencing
perdido, parada pedida) atravessa a gravação; antes, o escritor apaga do disco as chaves que
reservou. A manutenção pode ter descartado as linhas `pending` enquanto o worker pausava, antes
de o arquivo existir: depois disso nada mais veria o arquivo. Erro do `confirm` que não é
`LostFencing` não apaga nada: o commit pode ter acontecido sem a resposta chegar, e a linha que
seguir `pending` vai para o descarte da manutenção com a chave.
"""

import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import commit_candidate, create_candidate_object
from goblinshift.files.candidates import held_input_contents
from goblinshift.files.retention import remove_file_after_commit
from goblinshift.files.states import ArtifactRole
from goblinshift.files.storage import FileStorage
from goblinshift.runs.store import LostFencing, run_transaction
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.collection import (
    Collected,
    CollectionError,
    OutputFile,
    open_collected,
)

COPY_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class StoredCandidate:
    name: str
    role: ArtifactRole
    object_id: uuid.UUID
    # Só no preview: o nome da saída de conteúdo de que ele deriva.
    preview_of: str | None = None


class CandidateWriter:
    def __init__(self, factory: sessionmaker[Session], storage: FileStorage) -> None:
        self._factory = factory
        self._storage = storage

    def store(
        self,
        claimed: ClaimedAttempt,
        collected: Collected,
        *,
        lock_running: Callable[[Session], None],
        beating: Callable[[Iterable[bytes]], Iterator[bytes]],
    ) -> list[StoredCandidate]:
        """Um candidato por arquivo a publicar, na ordem de `_files_to_publish`."""
        files = _files_to_publish(collected)
        reused = self._reusable_objects(claimed, files)
        to_write = [
            (index, output) for index, (output, _role) in enumerate(files) if index not in reused
        ]

        def reserve(session: Session) -> list[tuple[uuid.UUID, str]]:
            lock_running(session)
            reserved = []
            for _ in to_write:
                candidate = create_candidate_object(
                    session,
                    claimed.owner_id,
                    claimed.run_id,
                    claimed.attempt_id,
                    claimed.generation,
                )
                reserved.append((candidate.object_id, candidate.storage_key))
            return reserved

        reserved = run_transaction(self._factory, reserve)
        keys = [key for _object_id, key in reserved]
        try:
            written = []
            for (_index, output), (_object_id, key) in zip(to_write, reserved, strict=True):
                with open_collected(output) as source:
                    chunks = beating(_chunks(source))
                    stored = self._storage.create_exclusive(key, chunks, output.size)
                if stored != (output.sha256, output.size):
                    raise CollectionError(f"out/ mudou depois da coleta: {output.name!r}")
                written.append(stored)
        except BaseException:
            _remove_reserved(self._storage, keys)
            raise

        def confirm(session: Session) -> None:
            lock_running(session)
            for (object_id, _key), (sha256, size) in zip(reserved, written, strict=True):
                commit_candidate(session, claimed.owner_id, object_id, sha256, size)

        try:
            run_transaction(self._factory, confirm)
        except LostFencing:
            _remove_reserved(self._storage, keys)
            raise
        object_ids = reused | {
            index: object_id
            for (index, _output), (object_id, _key) in zip(to_write, reserved, strict=True)
        }
        sources = _preview_sources(collected)
        return [
            StoredCandidate(output.name, role, object_ids[index], sources.get(output.name))
            for index, (output, role) in enumerate(files)
        ]

    def _reusable_objects(
        self, claimed: ClaimedAttempt, files: list[tuple[OutputFile, ArtifactRole]]
    ) -> dict[int, uuid.UUID]:
        """Índice da saída de conteúdo → objeto da entrada com o mesmo conteúdo, `committed`."""
        with self._factory() as session, session.begin():
            contents = held_input_contents(session, claimed.owner_id, claimed.input_upload_ids)
        # A primeira entrada com o conteúdo vence, como a primeira ocorrência do Detox.
        by_content: dict[tuple[bytes, int], uuid.UUID] = {}
        for content in contents:
            if content.committed:
                by_content.setdefault((content.sha256, content.size_bytes), content.object_id)
        reused = {}
        for index, (output, role) in enumerate(files):
            object_id = by_content.get((output.sha256, output.size))
            if role is ArtifactRole.OUTPUT and object_id is not None:
                reused[index] = object_id
        return reused


def _files_to_publish(collected: Collected) -> list[tuple[OutputFile, ArtifactRole]]:
    """Conteúdo na ordem do relatório, previews, relatório, manifesto e pacote (ADR 019).

    O preview vem depois de todo o conteúdo: a publicação precisa do artefato de origem já criado.
    """
    files = [(output, ArtifactRole.OUTPUT) for output in collected.outputs]
    files += [(preview.file, ArtifactRole.PREVIEW) for preview in collected.previews]
    files.append((collected.report_file, ArtifactRole.REPORT))
    if collected.manifest_file is not None:
        files.append((collected.manifest_file, ArtifactRole.MANIFEST))
    if collected.package_file is not None:
        files.append((collected.package_file, ArtifactRole.PACKAGE))
    return files


def _preview_sources(collected: Collected) -> dict[str, str]:
    return {preview.file.name: preview.of for preview in collected.previews}


def _remove_reserved(storage: FileStorage, keys: list[str]) -> None:
    """Apaga as chaves reservadas; a que não chegou a ser gravada é um `delete` sem efeito.

    Todas, não só as das cópias concluídas: assim também sai o arquivo de uma cópia que levante
    depois do `create_exclusive`, como uma conferência do conteúdo gravado. A falha de uma vira
    alerta com a chave (`purge_file_not_removed`) e não encobre o erro que levou à limpeza.
    """
    for key in keys:
        remove_file_after_commit(storage, key)


def _chunks(source: BinaryIO) -> Iterator[bytes]:
    while chunk := source.read(COPY_CHUNK_BYTES):
        yield chunk
