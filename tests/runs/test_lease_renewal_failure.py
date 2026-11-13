"""Falha transitória de banco na renovação do lease (docs/GoblinShiftPlan.md, 3.4 e 3.5).

A falha não pode encerrar a supervisão: o executor continua vivo, e abandoná-lo deixaria a área de
trabalho ocupando o worker, o que faz a Run seguinte receber `work_area_dirty` e gastar claims. A
trava da Run provoca um `55P03` de verdade; o pool esgotado e os erros que não são transitórios
não dependem de banco.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import psycopg
import pytest
from psycopg import errors as psycopg_errors
from sqlalchemy import Engine, select
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.settings import Settings
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER, Status
from goblinshift.runs.store import LostFencing
from goblinshift.runs.tables import run
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.coordinator import Coordinator, CoordinatorConfig
from goblinshift.runs.worker.runner import (
    ExecutorPhase,
    ExecutorStatus,
    LocalRunner,
    Runner,
    RunnerError,
)
from tests.runs.worker_support import (
    attempts,
    claim,
    coordinator,
    create_user,
    local_runner,
    queue_run,
    run_row,
)

integration = pytest.mark.integration

WAIT_S = 20.0
COORDINATOR_LOGGER = "goblinshift.runs.worker.coordinator"
RENEWAL_FAILED = "lease não renovado"
LOCK_TIMEOUT_MS = 100
# Lease longo: a renovação regular fica a 10 s, e só a nova tentativa (1 s) cabe em `RETRY_WAIT_S`.
LONG_LEASE = timedelta(seconds=30)
RETRY_WAIT_S = 5.0
LOCK_NOT_AVAILABLE = "55P03"
# Folga para a supervisão sem banco, que normalmente termina em milissegundos.
SUPERVISION_LIMIT_S = 5.0


@pytest.fixture
def worker_factory(engine: Engine, db_settings: Settings) -> Iterator[sessionmaker[Session]]:
    """Sessões do worker, com `lock_timeout` curto; `engine` já deixou o banco pronto."""
    short_lock = make_engine(db_settings.model_copy(update={"db_lock_timeout_ms": LOCK_TIMEOUT_MS}))
    yield make_session_factory(short_lock)
    short_lock.dispose()


@contextmanager
def run_locked_by_other_session(
    factory: sessionmaker[Session], run_id: uuid.UUID
) -> Iterator[None]:
    with factory() as session, session.begin():
        session.execute(select(run.c.id).where(run.c.id == run_id).with_for_update())
        yield


def started_attempt(
    worker: Coordinator,
    runner: LocalRunner,
    factory: sessionmaker[Session],
    storage: FileStorage,
) -> ClaimedAttempt:
    claimed = claim(factory, storage)
    worker.prepare(claimed)
    runner.start(claimed.attempt_id, claimed.executor_image)
    worker.record_start(claimed)
    return claimed


def renewal_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == RENEWAL_FAILED]


def wait_until(condition: Callable[[], object], wait_s: float = WAIT_S) -> None:
    deadline = time.monotonic() + wait_s
    while not condition():
        assert time.monotonic() < deadline, "condição não chegou a tempo"
        threading.Event().wait(0.02)


@integration
def test_executor_segue_supervisionado_depois_de_falha_do_banco_e_a_run_publica(
    factory: sessionmaker[Session],
    worker_factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root, "sleep", "1")
    worker = coordinator(worker_factory, storage, runner)
    claimed = started_attempt(worker, runner, factory, storage)

    with caplog.at_level(logging.WARNING, logger=COORDINATOR_LOGGER), ThreadPoolExecutor(1) as pool:
        with run_locked_by_other_session(factory, run_id):
            supervision = pool.submit(worker.supervise, claimed)
            wait_until(lambda: renewal_failures(caplog) or supervision.done())
        status = supervision.result(timeout=WAIT_S)
    applied = worker.finish(claimed, status)

    assert (status.phase, status.exit_code) == (ExecutorPhase.EXITED, 0)
    assert applied.state.status is Status.SUCCEEDED
    assert run_row(factory, run_id)["status"] == "succeeded"
    [only] = attempts(factory, run_id)
    assert (only["error_code"], only["ended_at"] is not None) == (None, True)


@integration
def test_renovacao_e_repetida_logo_depois_da_falha_sem_esperar_o_proximo_terco_do_lease(
    factory: sessionmaker[Session],
    worker_factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root, "sleep", "60")
    config = CoordinatorConfig(
        lease=LONG_LEASE, status_poll=timedelta(seconds=0.02), jitter=lambda: MIN_RETRY_JITTER
    )
    worker = Coordinator(worker_factory, storage, runner, config)
    claimed = started_attempt(worker, runner, factory, storage)

    with caplog.at_level(logging.WARNING, logger=COORDINATOR_LOGGER), ThreadPoolExecutor(1) as pool:
        with run_locked_by_other_session(factory, run_id):
            supervision = pool.submit(worker.supervise, claimed)
            wait_until(lambda: renewal_failures(caplog) or supervision.done())
            lease_during_failure = attempts(factory, run_id)[0]["lease_expires_at"]
        try:
            wait_until(
                lambda: (
                    attempts(factory, run_id)[0]["lease_expires_at"] > lease_during_failure
                    or supervision.done()
                ),
                RETRY_WAIT_S,
            )
        finally:
            runner.stop(claimed.attempt_id, timedelta(seconds=1))
        supervision.result(timeout=WAIT_S)

    assert attempts(factory, run_id)[0]["lease_expires_at"] > lease_during_failure


@integration
def test_log_da_falha_traz_o_sqlstate_e_nao_a_mensagem_do_banco(
    factory: sessionmaker[Session],
    worker_factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root, "sleep", "1")
    worker = coordinator(worker_factory, storage, runner)
    claimed = started_attempt(worker, runner, factory, storage)

    with caplog.at_level(logging.WARNING, logger=COORDINATOR_LOGGER), ThreadPoolExecutor(1) as pool:
        with run_locked_by_other_session(factory, run_id):
            supervision = pool.submit(worker.supervise, claimed)
            wait_until(lambda: renewal_failures(caplog) or supervision.done())
        supervision.result(timeout=WAIT_S)

    fields = cast(dict[str, object], renewal_failures(caplog)[0].__dict__["fields"])
    assert fields == {
        "run_id": str(claimed.run_id),
        "attempt_id": str(claimed.attempt_id),
        "generation": claimed.generation,
        "attempt.number": claimed.number,
        "error_type": "OperationalError",
        "sqlstate": LOCK_NOT_AVAILABLE,
    }


class _RunningUntilRenewed:
    """Executor que roda até o lease ser renovado `renewals_needed` vezes com sucesso.

    Sem renovação bem-sucedida, a supervisão só terminaria no teto de 10 min da tentativa. O limite
    reprova o teste antes disso, com `pytest.fail`, que nenhum `except Exception` captura.
    """

    def __init__(self, renewals: list[Status], renewals_needed: int) -> None:
        self._renewals = renewals
        self._renewals_needed = renewals_needed
        self._deadline = time.monotonic() + SUPERVISION_LIMIT_S

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        del attempt_id
        if time.monotonic() >= self._deadline:
            pytest.fail("a supervisão não terminou dentro do limite do teste")
        if len(self._renewals) < self._renewals_needed:
            return ExecutorStatus(ExecutorPhase.RUNNING)
        return ExecutorStatus(ExecutorPhase.EXITED, exit_code=0)


def worker_without_database(runner: object) -> Coordinator:
    config = CoordinatorConfig(
        lease=timedelta(seconds=0.3),
        status_poll=timedelta(seconds=0.001),
        jitter=lambda: MIN_RETRY_JITTER,
    )
    return Coordinator(
        cast(sessionmaker[Session], None),
        cast(FileStorage, None),
        cast(Runner, runner),
        config,
    )


def attempt_without_database() -> ClaimedAttempt:
    fields: dict[str, Any] = {
        "run_id": uuid.uuid4(),
        "attempt_id": uuid.uuid4(),
        "generation": 1,
        "number": 1,
    }
    return cast(ClaimedAttempt, SimpleNamespace(**fields))


def test_pool_esgotado_na_renovacao_tambem_e_transitorio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renewals: list[Status] = []
    worker = worker_without_database(_RunningUntilRenewed(renewals, renewals_needed=1))
    failures = iter([PoolTimeoutError("pool esgotado")])

    def renew(_claimed: ClaimedAttempt) -> Status:
        for failure in failures:
            raise failure
        renewals.append(Status.RUNNING)
        return Status.RUNNING

    monkeypatch.setattr(worker, "renew_lease", renew)

    status = worker.supervise(attempt_without_database())

    assert status.exit_code == 0
    assert renewals == [Status.RUNNING]


@pytest.mark.parametrize(
    "sqlstate",
    [
        pytest.param("28P01", id="senha-recusada"),
        pytest.param("53100", id="disco-cheio"),
        pytest.param("54000", id="limite-do-servidor"),
        pytest.param("58030", id="erro-de-e-s"),
    ],
)
def test_erro_operacional_que_pede_acao_de_fora_tambem_nao_abandona_o_executor(
    monkeypatch: pytest.MonkeyPatch, sqlstate: str
) -> None:
    renewals: list[Status] = []
    worker = worker_without_database(_RunningUntilRenewed(renewals, renewals_needed=1))
    driver_error = psycopg_errors.lookup(sqlstate)()
    failures = iter([DBAPIError.instance("stmt", {}, driver_error, psycopg.Error)])

    def renew(_claimed: ClaimedAttempt) -> Status:
        for failure in failures:
            raise failure
        renewals.append(Status.RUNNING)
        return Status.RUNNING

    monkeypatch.setattr(worker, "renew_lease", renew)

    status = worker.supervise(attempt_without_database())

    assert status.exit_code == 0
    assert renewals == [Status.RUNNING]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ProgrammingError("stmt", {}, Exception("x")), id="erro-de-programacao"),
        pytest.param(IntegrityError("stmt", {}, Exception("x")), id="violacao-de-constraint"),
        pytest.param(LostFencing("outra tentativa assumiu"), id="fencing-perdido"),
    ],
)
def test_erro_que_nao_e_falha_transitoria_do_banco_interrompe_a_supervisao(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    worker = worker_without_database(_RunningUntilRenewed([], renewals_needed=1))

    def renew(_claimed: ClaimedAttempt) -> Status:
        raise error

    monkeypatch.setattr(worker, "renew_lease", renew)

    with pytest.raises(type(error)):
        worker.supervise(attempt_without_database())


# --- `stop` que falha durante a supervisão ------------------------------------------------------

STOP_FAILED = "executor não parou"
# 128 + SIGTERM: o código de quem parou pelo `stop`.
STOPPED_EXIT_CODE = 143


class _StopFailsOnce:
    """Executor que só termina pelo `stop`; o primeiro `stop` falha com `RunnerError`.

    `events` grava renovações e paradas na ordem: o lease tem de seguir renovado entre o `stop`
    que falhou e o que funcionou, senão outra tentativa assumiria um executor ainda vivo.
    """

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.stops = 0
        self._stopped = False
        self._deadline = time.monotonic() + SUPERVISION_LIMIT_S

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        del attempt_id
        if time.monotonic() >= self._deadline:
            pytest.fail("a supervisão não terminou dentro do limite do teste")
        if self._stopped:
            return ExecutorStatus(ExecutorPhase.EXITED, exit_code=STOPPED_EXIT_CODE)
        return ExecutorStatus(ExecutorPhase.RUNNING)

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        self.stops += 1
        if self.stops == 1:
            self.events.append("stop falhou")
            raise RunnerError("executor-ctl fora do ar")
        self.events.append("stop")
        self._stopped = True
        return self.status(attempt_id)


def _stop_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == COORDINATOR_LOGGER and r.msg == STOP_FAILED]


def test_stop_que_falha_mantem_lease_e_tenta_de_novo(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A Run pediu parada (cancelamento): cada renovação devolve STOPPING e pede `stop`.
    events: list[str] = []
    runner = _StopFailsOnce(events)
    worker = worker_without_database(runner)

    def renew(_claimed: ClaimedAttempt) -> Status:
        events.append("lease")
        return Status.STOPPING

    monkeypatch.setattr(worker, "renew_lease", renew)
    claimed = attempt_without_database()

    with caplog.at_level(logging.WARNING, logger=COORDINATOR_LOGGER):
        status = worker.supervise(claimed)

    assert status == ExecutorStatus(ExecutorPhase.EXITED, exit_code=STOPPED_EXIT_CODE)
    assert runner.stops == 2
    # Sem desfecho depois do `stop` que falhou: a supervisão renovou o lease e só então repetiu.
    assert events == ["lease", "stop falhou", "lease", "stop"]
    [record] = _stop_failures(caplog)
    fields = cast(dict[str, object], record.__dict__["fields"])
    assert fields["attempt_id"] == str(claimed.attempt_id)
    assert fields["error"] == "executor-ctl fora do ar"


def test_stop_do_teto_da_tentativa_que_falha_tenta_de_novo_e_marca_o_estouro(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[str] = []
    runner = _StopFailsOnce(events)
    worker = Coordinator(
        cast(sessionmaker[Session], None),
        cast(FileStorage, None),
        cast(Runner, runner),
        CoordinatorConfig(
            lease=timedelta(seconds=30),
            status_poll=timedelta(seconds=0.001),
            attempt_timeout=timedelta(0),
            jitter=lambda: MIN_RETRY_JITTER,
        ),
    )

    def renew(_claimed: ClaimedAttempt) -> Status:
        events.append("lease")
        return Status.RUNNING

    monkeypatch.setattr(worker, "renew_lease", renew)

    with caplog.at_level(logging.WARNING, logger=COORDINATOR_LOGGER):
        status = worker.supervise(attempt_without_database())

    # O `stop` que falhou não vira desfecho; o seguinte confirma o término e marca o teto.
    assert status.timed_out
    assert status.phase is ExecutorPhase.EXITED
    assert runner.stops == 2
    assert events == ["lease", "stop falhou", "stop"]
    assert len(_stop_failures(caplog)) == 1
