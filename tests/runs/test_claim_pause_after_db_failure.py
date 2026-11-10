"""Falha de banco fora da supervisão deixa tentativa órfã; o worker pausa os claims (auditoria D3).

Cenário: a falha de banco num dos pontos sem tratamento (`record_start`, renovação do lease no
empacotamento pelo `_LeaseKeeper`, desfecho em `finish`) sobe até a volta do worker com a Attempt
viva e a área de trabalho ocupada. O `executor-ctl` recusa outro `start` com área suja
(`work_area_dirty`): sem a pausa, a Run seguinte, de outra conta, gasta um claim por um defeito
que não é dela. O `record_start` falha com um `55P03` de verdade: a Run fica travada noutra
sessão só entre o `start` e o fim do `record_start`, para a falha não vir de um passo seguinte.
Nos outros dois pontos a falha é injetada com o mesmo erro do driver.
"""

import logging
import os
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
from opentelemetry.trace import NoOpTracer
from psycopg import errors as psycopg_errors
from sqlalchemy import Engine, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.worker import _work_once
from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.finished import RunFinishedReporter
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.tables import attempt, run
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.claim_pause import ClaimPause
from goblinshift.runs.worker.coordinator import Coordinator
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from goblinshift.runs.worker.runner import ExecutorStatus
from tests.runs.fake_executor_ctl import IMAGE, FakeExecutorCtl
from tests.runs.worker_support import (
    LEASE,
    attempts,
    coordinator,
    create_user,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

REAL_EXECUTOR = (sys.executable, "-m", "goblinshift.processing.executor")
LOCK_TIMEOUT_MS = 100
WINDOW = timedelta(seconds=100)
NO_PAUSE = timedelta(0)
COORDINATOR_LOGGER = "goblinshift.runs.worker.coordinator"
START_REFUSED = "executor não iniciou"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def lock_not_available() -> Exception:
    return DBAPIError.instance("stmt", {}, psycopg_errors.LockNotAvailable(), psycopg.Error)


@pytest.fixture
def worker_factory(engine: Engine, db_settings: Settings) -> Iterator[sessionmaker[Session]]:
    """Sessões do worker, com `lock_timeout` curto; `engine` já deixou o banco pronto."""
    short_lock = make_engine(db_settings.model_copy(update={"db_lock_timeout_ms": LOCK_TIMEOUT_MS}))
    yield make_session_factory(short_lock)
    short_lock.dispose()


@pytest.fixture
def area(tmp_path: Path) -> Path:
    area = tmp_path / "exec-work"
    area.mkdir()
    return area


@pytest.fixture
def ctl(area: Path) -> Iterator[FakeExecutorCtl]:
    fake = FakeExecutorCtl(area, command=REAL_EXECUTOR)
    yield fake
    fake.close()


class LockingAfterStart(ExecutorCtlRunner):
    """Runner que, logo depois do `start`, trava a Run noutra sessão até o fim do teste.

    O `record_start` seguinte encontra a Run travada e recebe `55P03` do banco.
    """

    def __init__(
        self, ctl: FakeExecutorCtl, area: Path, lock_run: Callable[[uuid.UUID], None]
    ) -> None:
        super().__init__(ctl.socket_path, area, work_group=os.getgid())
        self._lock_run = lock_run
        self._locked = False

    def start(self, attempt_id: uuid.UUID, image: str) -> ExecutorStatus:
        status = super().start(attempt_id, image)
        if not self._locked:
            self._locked = True
            self._lock_run(attempt_id)
        return status


class RunLock:
    """Trava a Run de uma tentativa numa sessão própria, até `release`."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self._held = ExitStack()

    def lock(self, attempt_id: uuid.UUID) -> None:
        session = self._held.enter_context(self._factory())
        self._held.enter_context(session.begin())
        run_id = session.execute(
            select(attempt.c.run_id).where(attempt.c.id == attempt_id)
        ).scalar_one()
        session.execute(select(run.c.id).where(run.c.id == run_id).with_for_update())

    def release(self) -> None:
        self._held.close()


def fail_in_record_start(
    worker: Coordinator, run_lock: RunLock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Solta a trava ao sair do `record_start`, falhe ele ou não: se ele absorvesse o erro, o
    # desfecho publicaria sem falha, e a pausa não aconteceria.
    record_start = worker.record_start

    def record_start_then_release(claimed: ClaimedAttempt) -> None:
        try:
            record_start(claimed)
        finally:
            run_lock.release()

    monkeypatch.setattr(worker, "record_start", record_start_then_release)


def fail_in_lease_keeper(worker: Coordinator, monkeypatch: pytest.MonkeyPatch) -> None:
    # A supervisão absorve a falha da renovação (#217); o `_LeaseKeeper` do empacotamento não.
    def renew(_claimed: ClaimedAttempt) -> Status:
        raise lock_not_available()

    monkeypatch.setattr(worker, "renew_lease", renew)


def fail_in_finish(worker: Coordinator, monkeypatch: pytest.MonkeyPatch) -> None:
    def publish(*_args: object) -> object:
        raise lock_not_available()

    monkeypatch.setattr(worker, "publish", publish)


Inject = Callable[[Coordinator, pytest.MonkeyPatch], None]
POINTS = [
    pytest.param(None, id="record_start"),
    pytest.param(fail_in_lease_keeper, id="lease_keeper"),
    pytest.param(fail_in_finish, id="finish"),
]


@contextmanager
def worker_failing_at(
    point: Inject | None,
    factory: sessionmaker[Session],
    worker_factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    area: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Coordinator]:
    run_lock = RunLock(factory)
    try:
        if point is None:
            runner: ExecutorCtlRunner = LockingAfterStart(ctl, area, run_lock.lock)
        else:
            runner = ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid())
        worker = coordinator(worker_factory, storage, runner)
        if point is None:
            fail_in_record_start(worker, run_lock, monkeypatch)
        else:
            point(worker, monkeypatch)
        yield worker
    finally:
        run_lock.release()


def turn(
    factory: sessionmaker[Session], storage: FileStorage, worker: Coordinator, pause: ClaimPause
) -> bool:
    finished = RunFinishedReporter(factory, UserHasher(b"k" * 32), NoOpTracer())
    return _work_once(factory, storage, LEASE, worker, finished, pause)


def queue_two_runs(
    factory: sessionmaker[Session], storage: FileStorage
) -> tuple[uuid.UUID, uuid.UUID]:
    first = queue_run(factory, storage, create_user(factory, "ana"), executor_image=IMAGE)
    second = queue_run(factory, storage, create_user(factory, "bia"), executor_image=IMAGE)
    return first, second


@pytest.mark.parametrize("point", POINTS)
def test_falha_de_banco_deixa_a_tentativa_orfa_e_a_run_seguinte_espera_a_pausa(
    point: Inject | None,
    factory: sessionmaker[Session],
    worker_factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    area: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = queue_two_runs(factory, storage)
    clock = FakeClock()
    pause = ClaimPause(WINDOW, clock=clock)

    with worker_failing_at(point, factory, worker_factory, storage, ctl, area, monkeypatch) as w:
        first_turn = turn(factory, storage, w, pause)
        clock.now += WINDOW.total_seconds() - 0.001
        second_turn = turn(factory, storage, w, pause)

    assert (first_turn, second_turn) == (False, False)
    [orphan] = attempts(factory, first)
    assert orphan["ended_at"] is None
    assert any(area.iterdir()), "a área da tentativa órfã devia continuar ocupada"
    waiting = run_row(factory, second)
    assert (waiting["status"], waiting["claim_count"]) == ("queued", 0)
    assert attempts(factory, second) == []


@pytest.mark.parametrize("point", POINTS)
def test_sem_a_pausa_a_run_seguinte_gasta_claim_com_area_suja(
    point: Inject | None,
    factory: sessionmaker[Session],
    worker_factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    area: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """O defeito que a pausa evita: com janela zero, o `start` da Run seguinte é recusado."""
    first, second = queue_two_runs(factory, storage)
    pause = ClaimPause(NO_PAUSE, clock=FakeClock())

    with (
        worker_failing_at(point, factory, worker_factory, storage, ctl, area, monkeypatch) as w,
        caplog.at_level(logging.WARNING, logger=COORDINATOR_LOGGER),
    ):
        turn(factory, storage, w, pause)
        turn(factory, storage, w, pause)

    assert attempts(factory, first)[0]["ended_at"] is None
    burned = run_row(factory, second)
    assert (burned["status"], burned["phase"], burned["claim_count"]) == (
        "running",
        "retry_wait",
        1,
    )
    [refused] = attempts(factory, second)
    assert (refused["error_code"], refused["executor_started_at"]) == (
        "EXECUTOR_UNAVAILABLE",
        None,
    )
    [refusal] = [r for r in caplog.records if r.getMessage() == START_REFUSED]
    assert "work_area_dirty" in str(refusal.__dict__["fields"]["error"])
