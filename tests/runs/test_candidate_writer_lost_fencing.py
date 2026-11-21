"""Candidato gravado por um worker que perdeu o fencing não fica no disco (#442, 17e).

O worker reserva os candidatos, pausa além do lease, e a manutenção descarta as linhas `pending`
e encerra a tentativa antes de o arquivo existir: o `delete` dela não acha nada. O worker volta,
grava o arquivo e o `confirm` levanta `LostFencing`. Sem limpeza do escritor, o arquivo ficaria
em `runs/<run>/g<n>/` sem linha que o descarte veja.
"""

import uuid
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.candidates import discard_candidates
from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.store import LoadedRun, LostFencing
from goblinshift.runs.tables import attempt
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.collection import Collected, collect_outputs
from goblinshift.runs.worker.coordinator import Coordinator
from tests.runs.worker_support import (
    VALID,
    claim,
    coordinator,
    create_user,
    local_runner,
    queue_run,
)

pytestmark = pytest.mark.integration

UTF8_BOM = b"\xef\xbb\xbf"
# Com BOM a saída limpa difere da entrada: vira candidato gravado, não reaproveitamento.
NEEDS_WRITE = UTF8_BOM + VALID


def _collected_attempt(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> tuple[Coordinator, ClaimedAttempt, Collected]:
    """Tentativa executada e coletada, pronta para gravar os candidatos."""
    queue_run(factory, storage, create_user(factory), [("a.csv", NEEDS_WRITE)])
    runner = local_runner(work_root)
    worker = coordinator(factory, storage, runner)
    claimed = claim(factory, storage)
    worker.prepare(claimed)
    runner.start(claimed.attempt_id, claimed.executor_image)
    worker.record_start(claimed)
    worker.supervise(claimed)
    collected = collect_outputs(
        runner.work_dir(claimed.attempt_id) / "out", 1, recipe_id=claimed.recipe_id
    )
    return worker, claimed, collected


def _maintenance_discards_before_first_file(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    storage: FileStorage,
    attempt_id: uuid.UUID,
) -> None:
    """A manutenção descarta os candidatos antes de o primeiro arquivo aparecer no disco.

    Acontece depois da renovação do lease no primeiro bloco (o worker pausou logo em seguida):
    o `delete` das chaves descartadas não acha nada, e a cópia segue até o fim sem renovar.
    """
    real_create = storage.create_exclusive
    discarded: list[str] = []

    def discard_once() -> None:
        with unit_of_work(factory) as session:
            discarded.extend(discard_candidates(session, attempt_id))
            session.execute(
                update(attempt).where(attempt.c.id == attempt_id).values(ended_at=func.now())
            )
        for discarded_key in discarded:
            storage.delete(discarded_key)

    def discarding(chunks: Iterable[bytes]) -> Iterator[bytes]:
        for chunk in chunks:
            if not discarded:
                discard_once()
            yield chunk

    def create_while_discarded(
        key: str, chunks: Iterable[bytes], max_bytes: int
    ) -> tuple[bytes, int]:
        return real_create(key, discarding(chunks), max_bytes)

    monkeypatch.setattr(storage, "create_exclusive", create_while_discarded)


def _objects_of(factory: sessionmaker[Session], attempt_id: uuid.UUID) -> list[tuple[str, str]]:
    with factory() as session:
        rows = session.execute(
            select(stored_object.c.state, stored_object.c.storage_key).where(
                stored_object.c.attempt_id == attempt_id
            )
        ).all()
    return [(row.state, row.storage_key) for row in rows]


def _keys_on_disk(storage: FileStorage) -> set[str]:
    return {stored.key for stored in storage.walk_objects()}


def test_candidato_gravado_depois_do_descarte_e_apagado_pelo_escritor(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, claimed, collected = _collected_attempt(factory, storage, work_root)
    input_keys = _keys_on_disk(storage)
    _maintenance_discards_before_first_file(monkeypatch, factory, storage, claimed.attempt_id)

    with pytest.raises(LostFencing):
        worker.store_candidates(claimed, collected)

    assert _keys_on_disk(storage) == input_keys
    assert _objects_of(factory, claimed.attempt_id) == []


def test_falha_de_banco_no_confirm_nao_apaga_o_candidato(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # O commit pode ter acontecido sem a resposta chegar: o arquivo pode ser de um objeto
    # confirmado. Se a linha seguir `pending`, o descarte da manutenção cuida dele.
    worker, claimed, collected = _collected_attempt(factory, storage, work_root)
    input_keys = _keys_on_disk(storage)
    real_lock_running = worker._lock_running
    calls: list[int] = []

    def lock_running_failing_on_confirm(session: Session, fenced: ClaimedAttempt) -> LoadedRun:
        calls.append(len(calls))
        if len(calls) == 2:
            raise OperationalError("SELECT ... FOR UPDATE", None, Exception("conexão caiu"))
        return real_lock_running(session, fenced)

    monkeypatch.setattr(worker, "_lock_running", lock_running_failing_on_confirm)

    with pytest.raises(OperationalError):
        worker.store_candidates(claimed, collected)

    objects = _objects_of(factory, claimed.attempt_id)
    pending_keys = {key for state, key in objects if state == ObjectState.PENDING}
    assert len(objects) == len(pending_keys) > 0
    assert _keys_on_disk(storage) == input_keys | pending_keys
    assert all(storage.stat(key).size_bytes > 0 for key in pending_keys)


def test_falha_na_copia_apaga_os_candidatos_ja_gravados(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, claimed, collected = _collected_attempt(factory, storage, work_root)
    input_keys = _keys_on_disk(storage)
    real_create = storage.create_exclusive
    created: list[str] = []

    def disk_full_on_second_file(
        key: str, chunks: Iterable[bytes], max_bytes: int
    ) -> tuple[bytes, int]:
        if created:
            raise OSError("disco cheio")
        created.append(key)
        return real_create(key, chunks, max_bytes)

    monkeypatch.setattr(storage, "create_exclusive", disk_full_on_second_file)

    with pytest.raises(OSError, match="disco cheio"):
        worker.store_candidates(claimed, collected)

    assert len(created) == 1
    assert _keys_on_disk(storage) == input_keys
    # As linhas seguem `pending`: quem chama falha a tentativa e o descarte as remove.
    objects = _objects_of(factory, claimed.attempt_id)
    assert {state for state, _key in objects} == {ObjectState.PENDING}
    assert created[0] in {key for _state, key in objects}
