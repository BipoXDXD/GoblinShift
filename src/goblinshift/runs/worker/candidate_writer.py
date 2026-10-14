"""Saídas coletadas viram objetos candidatos (docs/GoblinShiftPlan.md, 3.4; ADRs 019 e 020).

Cada saída ganha um objeto `pending` em `runs/{run}/g{geração}/…`, numa transação curta; o
conteúdo é copiado de `out/` para o storage fora de transação e confirmado (`committed`) noutra
transação curta. Saída de conteúdo idêntica a uma entrada da Run, pelo SHA-256 e tamanho que a
coleta calculou, não é gravada de novo: o artefato aponta para o objeto da entrada enquanto ele
estiver `committed` (ADR 020; 1.4, "renomear não duplica objetos").

Quem chama decide o fencing e o lease: `lock_running` abre cada transação travando a Run, e
`beating` envolve a cópia para renovar o lease entre blocos. O que eles levantarem (fencing
perdido, parada pedida) atravessa a gravação sem ser tratado aqui.
"""

import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import commit_candidate, create_candidate_object
from goblinshift.files.candidates import held_input_contents
from goblinshift.files.states import ArtifactRole
from goblinshift.files.storage import FileStorage
from goblinshift.runs.store import run_transaction
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.collection import Collected, OutputFile

COPY_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class StoredCandidate:
    name: str
    role: ArtifactRole
    object_id: uuid.UUID


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
        written = []
        for (_index, output), (_object_id, key) in zip(to_write, reserved, strict=True):
            with output.path.open("rb") as source:
                chunks = beating(_chunks(source))
                written.append(self._storage.create_exclusive(key, chunks, output.size))

        def confirm(session: Session) -> None:
            lock_running(session)
            for (object_id, _key), (sha256, size) in zip(reserved, written, strict=True):
                commit_candidate(session, claimed.owner_id, object_id, sha256, size)

        run_transaction(self._factory, confirm)
        object_ids = reused | {
            index: object_id
            for (index, _output), (object_id, _key) in zip(to_write, reserved, strict=True)
        }
        return [
            StoredCandidate(output.name, role, object_ids[index])
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
    """Conteúdo na ordem do relatório, depois relatório, manifesto e pacote (ADR 019)."""
    files = [(output, ArtifactRole.OUTPUT) for output in collected.outputs]
    files.append((collected.report_file, ArtifactRole.REPORT))
    if collected.manifest_file is not None:
        files.append((collected.manifest_file, ArtifactRole.MANIFEST))
    if collected.package_file is not None:
        files.append((collected.package_file, ArtifactRole.PACKAGE))
    return files


def _chunks(source: BinaryIO) -> Iterator[bytes]:
    while chunk := source.read(COPY_CHUNK_BYTES):
        yield chunk
