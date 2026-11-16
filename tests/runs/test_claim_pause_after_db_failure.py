"""Falha de banco que escapa do coordenador deixa a tentativa órfã e pausa os claims.

Cenário: a falha de banco em `record_start`, na renovação do lease ou no desfecho sobe até a volta
do worker. No empacotamento, isso ocorre se a falha persistir até esgotar o prazo de retry.
A Attempt viva ocupa a área de trabalho. O `executor-ctl` recusa outro `start` com área suja
(`work_area_dirty`): sem a pausa, a Run seguinte, de outra conta, gasta um claim por um defeito que
não é dela. O `record_start` falha com um `55P03` de verdade: a Run fica travada noutra sessão só
entre o `start` e o fim do `record_start`, para a falha não vir de um passo seguinte. Nos outros
dois pontos a falha é injetada com o mesmo erro do driver.

A pausa vive na memória do processo. Depois de um reinício, quem segura o claim é o portão da área
de trabalho (`WorkAreaGate`), que lê a área que o `executor-ctl` confere. Os testes da pausa
desligam o portão (área vista como vazia) para que só ela decida a volta.
"""

import logging
import os
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path
from typing import cast

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
from goblinshift.runs.tables import attempt, run, run_output
from goblinshift.runs.worker import coordinator as worker_coordinator
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.claim_pause import ClaimPause
from goblinshift.runs.worker.collection import collect_outputs
from goblinshift.runs.worker.coordinator import Coordinator
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from goblinshift.runs.worker.runner import ExecutorStatus, Runner
from goblinshift.runs.worker.work_area_gate import WorkAreaGate
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
    # A falha contínua depois do `record_start` esgota o retry do empacotamento. A cópia da
    # entrada ainda renova normalmente, para que a tentativa chegue ao executor.
    started: set[uuid.UUID] = set()
    record_start = worker.record_start
    renew_lease = worker.renew_lease

    def record_start_and_mark(claimed: ClaimedAttempt) -> None:
        record_start(claimed)
        started.add(claimed.attempt_id)

    def renew(claimed: ClaimedAttempt) -> Status:
        if claimed.attempt_id in started:
            raise lock_not_available()
        return renew_lease(claimed)

    monkeypatch.setattr(worker, "record_start", record_start_and_mark)
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


class UnseenArea:
    """Área sempre vista como vazia: desliga o portão para isolar a pausa."""

    def list_work(self) -> list[uuid.UUID]:
        return []


GATE_OFF = WorkAreaGate(cast(Runner, UnseenArea()))


def turn(
    factory: sessionmaker[Session],
    storage: FileStorage,
    worker: Coordinator,
    pause: ClaimPause,
    gate: WorkAreaGate = GATE_OFF,
) -> bool:
    finished = RunFinishedReporter(factory, UserHasher(b"k" * 32), NoOpTracer())
    return _work_once(factory, storage, LEASE, worker, finished, pause, gate)


def queue_two_runs(
    factory: sessionmaker[Session], storage: FileStorage
) -> tuple[uuid.UUID, uuid.UUID]:
    first = queue_run(factory, storage, create_user(factory, "ana"), executor_image=IMAGE)
    second = queue_run(factory, storage, create_user(factory, "bia"), executor_image=IMAGE)
    return first, second


@pytest.mark.parametrize("stage", ["collection", "candidates"])
def test_falha_breve_na_renovacao_publica_na_mesma_tentativa(
    stage: str,
    factory: sessionmaker[Session],
    worker_factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    area: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory, "ana"), executor_image=IMAGE)
    worker = coordinator(
        worker_factory, storage, ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid())
    )
    renew = worker.renew_lease
    renewing_outputs = False
    failed = False

    def inject_during[**P, T](work: Callable[P, T]) -> Callable[P, T]:
        def run_stage(*args: P.args, **kwargs: P.kwargs) -> T:
            nonlocal renewing_outputs
            renewing_outputs = True
            try:
                return work(*args, **kwargs)
            finally:
                renewing_outputs = False

        return run_stage

    def renew_after_failure(claimed: ClaimedAttempt) -> Status:
        nonlocal failed
        if renewing_outputs and not failed:
            failed = True
            raise lock_not_available()
        return renew(claimed)

    if stage == "collection":
        monkeypatch.setattr(worker_coordinator, "collect_outputs", inject_during(collect_outputs))
    else:
        monkeypatch.setattr(worker, "store_candidates", inject_during(worker.store_candidates))
    monkeypatch.setattr(worker, "renew_lease", renew_after_failure)
    pause = ClaimPause(WINDOW, clock=FakeClock())

    assert turn(factory, storage, worker, pause)
    assert failed
    assert not pause.active
    assert run_row(factory, run_id)["status"] == "succeeded"
    [physical_attempt] = attempts(factory, run_id)
    assert physical_attempt["executor_started_at"] is not None
    assert physical_attempt["ended_at"] is not None
    with factory() as session:
        positions = (
            select(run_output.c.position)
            .where(run_output.c.run_id == run_id)
            .order_by(run_output.c.position)
        )
        assert session.scalars(positions).all() == [0, 1, 2, 3, 4]


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
    with factory() as session:
        outputs = select(run_output.c.artifact_id).where(run_output.c.run_id == first)
        assert session.scalars(outputs).all() == []
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


@pytest.mark.parametrize("point", POINTS)
def test_worker_reiniciado_dentro_da_janela_nao_gasta_claim_com_a_tentativa_orfa(
    point: Inject | None,
    factory: sessionmaker[Session],
    worker_factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    area: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deploy ou SIGKILL depois da falha: o processo novo nasce sem pausa, e a área o segura."""
    first, second = queue_two_runs(factory, storage)
    before_restart = ClaimPause(WINDOW, clock=FakeClock())
    restarted = ClaimPause(WINDOW, clock=FakeClock())
    gate = WorkAreaGate(ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid()))

    with worker_failing_at(point, factory, worker_factory, storage, ctl, area, monkeypatch) as w:
        turn(factory, storage, w, before_restart)
        second_turn = turn(factory, storage, w, restarted, gate)

    assert second_turn is False
    assert restarted.active is False
    [orphan] = attempts(factory, first)
    assert orphan["ended_at"] is None
    waiting = run_row(factory, second)
    assert (waiting["status"], waiting["claim_count"]) == ("queued", 0)
    assert attempts(factory, second) == []


def test_falha_de_banco_na_copia_da_entrada_pausa_os_claims_antes_do_executor(
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
    worker = coordinator(
        worker_factory, storage, ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid())
    )

    def renew(_claimed: ClaimedAttempt) -> Status:
        raise lock_not_available()

    monkeypatch.setattr(worker, "renew_lease", renew)
    first_turn = turn(factory, storage, worker, pause)
    clock.now += WINDOW.total_seconds() - 0.001
    second_turn = turn(factory, storage, worker, pause)

    assert (first_turn, second_turn) == (False, False)
    [orphan] = attempts(factory, first)
    assert (orphan["ended_at"], orphan["executor_started_at"]) == (None, None)
    assert any(area.iterdir()), "a área da tentativa órfã devia continuar ocupada"
    waiting = run_row(factory, second)
    assert (waiting["status"], waiting["claim_count"]) == ("queued", 0)
    assert attempts(factory, second) == []
