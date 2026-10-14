"""Empacotamento do coordenador sob falha e demora (docs/GoblinShiftPlan.md, 3.4 e 3.5).

PostgreSQL e executor reais. Depois que o executor termina, o worker coleta `out/` e grava os
candidatos no storage, fora do `supervise`. Duas garantias:
- erro de infraestrutura nessa etapa (disco cheio, storage) é falha transitória na hora, com
  retentativa, em vez de derrubar a volta do worker e esperar o lease vencer;
- o lease segue renovado durante a coleta e a gravação, para que uma cópia longa (o ZIP chega a
  512 MiB) não passe a tentativa viva para a manutenção.
"""

import errno
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage, ObjectAlreadyExistsError
from goblinshift.files.tables import stored_object
from goblinshift.runs.cancellation import cancel_run
from goblinshift.runs.tables import attempt, run_input, run_output
from goblinshift.runs.worker import candidate_writer, collection, package_check
from goblinshift.runs.worker import coordinator as coordinator_module
from goblinshift.runs.worker.collection import Collected
from tests.runs.worker_support import (
    LEASE,
    VALID,
    attempts,
    claim,
    coordinator,
    create_user,
    local_runner,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

UTF8_BOM = b"\xef\xbb\xbf"
# Com BOM a saída limpa difere da entrada: vira candidato gravado, não reaproveitamento.
NEEDS_WRITE = UTF8_BOM + VALID


class FailingStorage(FileStorage):
    """Storage cujas gravações falham a partir de `arm()`; as do upload, antes, passam."""

    def __init__(self, root: Path, error: Exception) -> None:
        super().__init__(root)
        self._error = error
        self._armed = False

    def arm(self) -> None:
        self._armed = True

    def create_exclusive(
        self, key: str, chunks: Iterable[bytes], max_bytes: int
    ) -> tuple[bytes, int]:
        if self._armed:
            raise self._error
        return super().create_exclusive(key, chunks, max_bytes)


class SlowStorage(FileStorage):
    """Storage lento: depois de `slow_down`, pausa a cada bloco e anota a folga do lease."""

    def __init__(self, root: Path, factory: sessionmaker[Session]) -> None:
        super().__init__(root)
        self._factory = factory
        self._pauses = 0
        self._pause_s = 0.0
        self._attempt_id: uuid.UUID | None = None
        self._on_pause: Callable[[], None] = lambda: None
        self.lease_margins: list[float] = []

    def slow_down(
        self,
        attempt_id: uuid.UUID,
        pauses: int,
        pause: timedelta,
        on_pause: Callable[[], None] = lambda: None,
    ) -> None:
        self._attempt_id = attempt_id
        self._pauses = pauses
        self._pause_s = pause.total_seconds()
        self._on_pause = on_pause

    def create_exclusive(
        self, key: str, chunks: Iterable[bytes], max_bytes: int
    ) -> tuple[bytes, int]:
        return super().create_exclusive(key, self._slowly(chunks), max_bytes)

    def _slowly(self, chunks: Iterable[bytes]) -> Iterator[bytes]:
        for chunk in chunks:
            yield chunk
            if self._pauses > 0:
                self._pauses -= 1
                self._on_pause()
                time.sleep(self._pause_s)
                self.lease_margins.append(self._lease_margin())

    def _lease_margin(self) -> float:
        with self._factory() as session:
            margin: timedelta = session.execute(
                select(attempt.c.lease_expires_at - func.now()).where(
                    attempt.c.id == self._attempt_id
                )
            ).scalar_one()
        return margin.total_seconds()


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(OSError(errno.ENOSPC, "No space left on device"), id="disco-cheio"),
        pytest.param(ObjectAlreadyExistsError("runs/x/g1/y"), id="erro-do-storage"),
    ],
)
def test_falha_ao_gravar_candidato_e_transitoria_na_hora(
    factory: sessionmaker[Session], tmp_path: Path, work_root: Path, error: Exception
) -> None:
    storage = FailingStorage(tmp_path / "storage", error)
    run_id = queue_run(factory, storage, create_user(factory), [("a.csv", NEEDS_WRITE)])
    claimed = claim(factory, storage)
    storage.arm()

    applied = coordinator(factory, storage, local_runner(work_root)).run(claimed)

    assert applied is not None
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["error_code"]) == ("running", "retry_wait", None)
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is not None
    assert only["error_code"] == "EXECUTOR_UNAVAILABLE"


def test_lease_segue_renovado_durante_a_gravacao_dos_candidatos(
    factory: sessionmaker[Session],
    tmp_path: Path,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Blocos de 4 bytes: a cópia vira muitas iterações, e cada uma pausa até passar do lease.
    monkeypatch.setattr(candidate_writer, "COPY_CHUNK_BYTES", 4)
    storage = SlowStorage(tmp_path / "storage", factory)
    run_id = queue_run(factory, storage, create_user(factory), [("a.csv", NEEDS_WRITE)])
    claimed = claim(factory, storage)
    pause = LEASE / 8
    storage.slow_down(claimed.attempt_id, pauses=16, pause=pause)

    applied = coordinator(factory, storage, local_runner(work_root)).run(claimed)

    assert applied is not None
    assert run_row(factory, run_id)["status"] == "succeeded"
    assert len(storage.lease_margins) == 16
    assert 16 * pause > LEASE
    assert min(storage.lease_margins) > 0


def test_cancelamento_durante_a_gravacao_e_percebido_na_renovacao(
    factory: sessionmaker[Session],
    tmp_path: Path,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(candidate_writer, "COPY_CHUNK_BYTES", 4)
    storage = SlowStorage(tmp_path / "storage", factory)
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner, [("a.csv", NEEDS_WRITE)])
    claimed = claim(factory, storage)
    canceled: list[bool] = []

    def cancel_once() -> None:
        if not canceled:
            canceled.append(True)
            cancel_run(factory, storage, owner, run_id)

    storage.slow_down(claimed.attempt_id, pauses=8, pause=LEASE / 8, on_pause=cancel_once)

    coordinator(factory, storage, local_runner(work_root)).run(claimed)

    # A cópia parou na renovação seguinte, sem esperar o fim da gravação.
    assert len(storage.lease_margins) < 8
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("canceled", "CANCELED")
    with factory() as session:
        states = session.execute(
            select(stored_object.c.state).where(stored_object.c.attempt_id == claimed.attempt_id)
        ).scalars()
        assert set(states) <= {ObjectState.DELETE_REQUESTED}


def test_pacote_adulterado_pelo_executor_vira_retentativa_sem_publicar_nada(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory), [("a.csv", VALID)])
    claimed = claim(factory, storage)

    coordinator(factory, storage, local_runner(work_root, "tamper-package")).run(claimed)

    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["summary"]) == (
        "running",
        "retry_wait",
        {"progress": {"processed": 1, "total": 1}},
    )
    [only] = attempts(factory, run_id)
    assert only["error_code"] == "EXECUTOR_UNAVAILABLE"
    with factory() as session:
        assert (
            session.execute(
                select(func.count()).select_from(run_output).where(run_output.c.run_id == run_id)
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                select(run_input.c.outcome).where(run_input.c.run_id == run_id)
            ).scalar_one()
            is None
        )


def test_lease_segue_renovado_durante_a_coleta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A coleta lê tudo de out/ (o ZIP chega a 512 MiB): cada bloco lido aqui demora.
    run_id = queue_run(factory, storage, create_user(factory), [("a.csv", NEEDS_WRITE)])
    claimed = claim(factory, storage)
    pause = LEASE / 8
    margins: list[float] = []
    monkeypatch.setattr(collection, "READ_CHUNK_BYTES", 4)
    monkeypatch.setattr(package_check, "READ_CHUNK_BYTES", 4)

    def slow_collect(*args: Any, **kwargs: Any) -> Collected:
        heartbeat = kwargs.pop("heartbeat", lambda: None)

        def slow_heartbeat() -> None:
            heartbeat()
            if len(margins) < 16:
                time.sleep(pause.total_seconds())
                margins.append(lease_margin(factory, claimed.attempt_id))

        return collection.collect_outputs(*args, heartbeat=slow_heartbeat, **kwargs)

    monkeypatch.setattr(f"{coordinator_module.__name__}.collect_outputs", slow_collect)

    coordinator(factory, storage, local_runner(work_root)).run(claimed)

    assert run_row(factory, run_id)["status"] == "succeeded"
    assert len(margins) == 16
    assert min(margins) > 0


def lease_margin(factory: sessionmaker[Session], attempt_id: uuid.UUID) -> float:
    with factory() as session:
        margin: timedelta = session.execute(
            select(attempt.c.lease_expires_at - func.now()).where(attempt.c.id == attempt_id)
        ).scalar_one()
    return margin.total_seconds()


# --- progress.jsonl é escrito pelo sandbox: lido sem seguir link e com teto ---------------------


def _progress_after_renew(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    plant: Callable[[Path], None],
) -> object:
    run_id = queue_run(factory, storage, create_user(factory), [("a.csv", VALID)])
    claimed = claim(factory, storage)
    runner = local_runner(work_root)
    worker = coordinator(factory, storage, runner)
    worker.prepare(claimed)
    plant(runner.work_dir(claimed.attempt_id) / "progress.jsonl")

    worker.renew_lease(claimed)

    return run_row(factory, run_id)["summary"]


def test_progresso_que_e_symlink_nao_e_seguido(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "fora.jsonl"
    outside.write_bytes(b'{"position": 0}\n' * 3)

    summary = _progress_after_renew(
        factory, storage, work_root, lambda progress: progress.symlink_to(outside)
    )

    assert summary == {"progress": {"processed": 0, "total": 1}}


def test_progresso_nunca_passa_do_total_de_entradas(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    summary = _progress_after_renew(factory, storage, work_root, _many_lines)

    assert summary == {"progress": {"processed": 1, "total": 1}}


def _many_lines(progress: Path) -> None:
    progress.write_bytes(b"\n" * 3_000_000)
