"""Supervisão do worker diante de falhas reais do PostgreSQL (levantamento L14-a).

`test_lease_renewal_failure.py` tem uma falha real (`55P03`); o pool esgotado e os SQLSTATEs de lá
são levantados à mão (`monkeypatch`, `DBAPIError.instance`). Aqui o banco falha de verdade durante
a renovação do lease de uma Attempt em curso: `statement_timeout` numa espera de trava (57014),
backend encerrado por `pg_terminate_backend` (57P01) e pool esgotado. O que a main promete hoje
(`Coordinator._try_renew`, ADR 047) e os testes afirmam: a falha não encerra a supervisão, o
executor não é parado nem abandonado, o log traz só o tipo e o SQLSTATE, a renovação seguinte
funciona e a Run publica como se nada tivesse acontecido.

Nenhum teste depende de tempo: o executor só "termina" (para a supervisão) depois que o teste
viu a falha e a recuperação (`_GatedRunner`), e a falha existe só enquanto o teste a mantém. Os
únicos tempos reais são os timeouts curtos que o próprio cenário exige.
"""

import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Connection, Engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.settings import Settings
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.tables import attempt, run
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.coordinator import Coordinator
from goblinshift.runs.worker.runner import ExecutorPhase, ExecutorStatus, LocalRunner
from tests.runs.worker_support import (
    attempts,
    claim,
    coordinator,
    create_user,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

COORDINATOR_LOGGER = "goblinshift.runs.worker.coordinator"
RENEWAL_FAILED = "lease não renovado"
ADMIN_SHUTDOWN = "57P01"
QUERY_CANCELED = "57014"
WAIT_S = 20.0
POLL_S = 0.01
# Curtos para o teste não esperar, e `lock_timeout` longo para que só o `statement_timeout` encerre
# a espera pela trava (55P03 não pode ser a causa do 57014).
STATEMENT_TIMEOUT_MS = 200
LONG_LOCK_TIMEOUT_MS = 60_000
POOL_TIMEOUT_S = 0.1


class _GatedRunner(LocalRunner):
    """Executor real que a supervisão enxerga como vivo até o teste liberá-lo.

    O processo termina quando quiser; `status` só o revela depois de `release`. Assim a supervisão
    dura exatamente o tempo da falha e da recuperação, sem `sleep` no executor, e o teste prova
    que nada parou nem removeu o executor enquanto o banco falhava (`stops`).
    """

    def __init__(self, work_root: Path) -> None:
        super().__init__(work_root, attempt_timeout=timedelta(seconds=30))
        self._gate = threading.Event()
        self.stops = 0

    def release(self) -> None:
        self._gate.set()

    def status(self, attempt_id: uuid.UUID) -> ExecutorStatus:
        if not self._gate.is_set():
            return ExecutorStatus(ExecutorPhase.RUNNING)
        return super().status(attempt_id)

    def stop(self, attempt_id: uuid.UUID, grace: timedelta) -> ExecutorStatus:
        self.stops += 1
        return super().stop(attempt_id, grace)


@dataclass(frozen=True)
class _DbState:
    """Linhas da Attempt e da Run como o banco as mostra a uma conexão de fora (admin)."""

    attempt: dict[str, Any]
    run: dict[str, Any]


@dataclass
class _Supervised:
    """O que a supervisão devolveu e o que o banco mostrou durante e depois da falha."""

    status: ExecutorStatus
    failures: list[logging.LogRecord]
    state_before: _DbState
    state_during_failure: _DbState
    lease_before: datetime
    lease_recovered: datetime
    attempt_ended_during_failure: bool
    stops: int


@dataclass
class _Scenario:
    worker: Coordinator
    runner: _GatedRunner
    claimed: ClaimedAttempt
    run_id: uuid.UUID
    factory: sessionmaker[Session]
    admin: Engine
    settings: Settings
    caplog: pytest.LogCaptureFixture
    finished: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def admin_engine(db_settings: Settings) -> Iterator[Engine]:
    """Conexão de fora do pool do worker, para encerrar backends e segurar travas."""
    admin = make_engine(db_settings)
    yield admin
    admin.dispose()


def _worker_engine(db_settings: Settings, **settings: object) -> Iterator[Engine]:
    """Engine do worker, com os limites do cenário (o fixture `engine` já migrou o banco)."""
    tuned = make_engine(db_settings.model_copy(update=settings))
    yield tuned
    tuned.dispose()


@pytest.fixture
def worker_engine(engine: Engine, db_settings: Settings) -> Iterator[Engine]:
    yield from _worker_engine(db_settings)


@pytest.fixture
def statement_timeout_engine(engine: Engine, db_settings: Settings) -> Iterator[Engine]:
    yield from _worker_engine(
        db_settings,
        db_statement_timeout_ms=STATEMENT_TIMEOUT_MS,
        db_lock_timeout_ms=LONG_LOCK_TIMEOUT_MS,
    )


@pytest.fixture
def single_connection_engine(engine: Engine, db_settings: Settings) -> Iterator[Engine]:
    yield from _worker_engine(
        db_settings, db_pool_size=1, db_pool_max_overflow=0, db_pool_timeout_s=POOL_TIMEOUT_S
    )


def _scenario(
    engine: Engine,
    factory: sessionmaker[Session],
    admin: Engine,
    settings: Settings,
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> _Scenario:
    """Attempt iniciada: o worker (com o engine do cenário) já fez `prepare` e `record_start`."""
    run_id = queue_run(factory, storage, create_user(factory))
    runner = _GatedRunner(work_root)
    worker_factory = make_session_factory(engine)
    worker = coordinator(worker_factory, storage, runner)
    claimed = claim(factory, storage)
    worker.prepare(claimed)
    runner.start(claimed.attempt_id, claimed.executor_image)
    worker.record_start(claimed)
    return _Scenario(worker, runner, claimed, run_id, factory, admin, settings, caplog)


def _lease(scenario: _Scenario) -> datetime:
    return cast(datetime, attempts(scenario.factory, scenario.run_id)[0]["lease_expires_at"])


def _db_state(admin: Engine, attempt_id: uuid.UUID, run_id: uuid.UUID) -> _DbState:
    """Lê a Attempt e a Run por uma conexão de fora, que só enxerga o que foi commitado."""
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        attempt_row = conn.execute(select(attempt).where(attempt.c.id == attempt_id)).one()
        run_row_ = conn.execute(select(run).where(run.c.id == run_id)).one()
    return _DbState(dict(attempt_row._mapping), dict(run_row_._mapping))


def _state(scenario: _Scenario) -> _DbState:
    return _db_state(scenario.admin, scenario.claimed.attempt_id, scenario.run_id)


def _assert_lease_not_half_renewed(before: _DbState, during_failure: _DbState) -> None:
    """Com a renovação falhada, o `UPDATE attempt` e o `UPDATE run` não ficaram commitados."""
    assert during_failure.attempt == before.attempt
    assert during_failure.run == before.run


def _renewal_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == RENEWAL_FAILED]


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    return cast(dict[str, Any], record.__dict__["fields"])


def _wait_until(condition: Callable[[], object], what: str, wait_s: float = WAIT_S) -> None:
    deadline = time.monotonic() + wait_s
    while not condition():
        assert time.monotonic() < deadline, f"{what}: não chegou a tempo"
        threading.Event().wait(POLL_S)


def _supervise_through(
    scenario: _Scenario, fault: Callable[[], AbstractContextManager[None]]
) -> _Supervised:
    """Supervisiona a Attempt com `fault` ativa, depois a desfaz e espera a renovação voltar.

    Ordem: a falha nasce antes da supervisão; o teste espera a primeira renovação falhar; desfaz a
    falha; espera o lease avançar (renovação bem-sucedida); só então libera o executor.
    """
    caplog = scenario.caplog
    state_before = _state(scenario)
    lease_before = _lease(scenario)
    with caplog.at_level(logging.WARNING, logger=COORDINATOR_LOGGER), ThreadPoolExecutor(1) as pool:
        try:
            with fault():
                supervision = pool.submit(scenario.worker.supervise, scenario.claimed)
                _wait_until(
                    lambda: _renewal_failures(caplog) or supervision.done(), "renovação falhar"
                )
                state_during_failure = _state(scenario)
                ended_during_failure = state_during_failure.attempt["ended_at"] is not None
            _wait_until(
                lambda: _lease(scenario) > lease_before or supervision.done(),
                "renovação voltar",
            )
            lease_recovered = _lease(scenario)
        finally:
            scenario.runner.release()
        status = supervision.result(timeout=WAIT_S)
    return _Supervised(
        status=status,
        failures=_renewal_failures(caplog),
        state_before=state_before,
        state_during_failure=state_during_failure,
        lease_before=lease_before,
        lease_recovered=lease_recovered,
        attempt_ended_during_failure=ended_during_failure,
        stops=scenario.runner.stops,
    )


def _assert_survived_and_published(scenario: _Scenario, outcome: _Supervised) -> None:
    """A falha foi absorvida: executor vivo e sem `stop`, lease renovado, e a Run publica."""
    assert outcome.stops == 0
    assert not outcome.attempt_ended_during_failure
    assert outcome.lease_recovered > outcome.lease_before
    assert (outcome.status.phase, outcome.status.exit_code) == (ExecutorPhase.EXITED, 0)
    applied = scenario.worker.finish(scenario.claimed, outcome.status)
    assert applied.state.status is Status.SUCCEEDED
    assert run_row(scenario.factory, scenario.run_id)["status"] == "succeeded"
    [only] = attempts(scenario.factory, scenario.run_id)
    assert (only["error_code"], only["ended_at"] is not None) == (None, True)


def _assert_failure_fields(
    scenario: _Scenario, outcome: _Supervised, error_type: str, sqlstate: str | None
) -> None:
    """Só o tipo e o SQLSTATE vão ao log, junto das identidades da Attempt."""
    assert outcome.failures
    _assert_log_has_no_connection_data(scenario, outcome.failures)
    expected = {
        "run_id": str(scenario.claimed.run_id),
        "attempt_id": str(scenario.claimed.attempt_id),
        "generation": scenario.claimed.generation,
        "attempt.number": scenario.claimed.number,
        "error_type": error_type,
        "sqlstate": sqlstate,
    }
    assert [_fields(record) for record in outcome.failures] == [expected] * len(outcome.failures)


def _assert_log_has_no_connection_data(
    scenario: _Scenario, failures: list[logging.LogRecord]
) -> None:
    """Sem traceback e sem canário da conexão (host, porta, usuário, banco, senha) no log.

    A mensagem do `OperationalError` traz host, porta e usuário; por isso o coordinator só loga o
    tipo e o SQLSTATE e não usa `exc_info`. Os canários vêm da URL de teste (`db_settings`).
    """
    assert all(record.exc_info is None and record.exc_text is None for record in failures)
    settings = scenario.settings
    canaries = [
        settings.db_host,
        str(settings.db_port),
        settings.db_user,
        settings.db_name,
        settings.db_password.get_secret_value(),
    ]
    logged = "\n".join(f"{record.getMessage()} {record.__dict__['fields']}" for record in failures)
    assert [canary for canary in canaries if canary in logged] == []
    assert [canary for canary in canaries if canary in scenario.caplog.text] == []


# Backend encerrado ------------------------------------------------------------------------

_TARGET_BACKEND = (
    "FROM pg_stat_activity WHERE datname = current_database()"
    " AND backend_type = 'client backend' AND pid = :pid"
)
TERMINATE_BACKEND = text(f"SELECT pg_terminate_backend(pid) {_TARGET_BACKEND}")
COUNT_BACKEND = text(f"SELECT count(*) {_TARGET_BACKEND}")


def _terminate_and_wait(admin: Engine, pid: int) -> None:
    """`pg_terminate_backend` de um backend até ele sumir de `pg_stat_activity`.

    O sinal é assíncrono; esperar o backend sair tira a corrida entre a morte dele e o próximo
    comando do cliente. AUTOCOMMIT: o snapshot das estatísticas vale por transação, e uma
    transação aberta repetiria sempre a mesma resposta.
    """
    params = {"pid": pid}
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        assert conn.execute(TERMINATE_BACKEND, params).scalar_one() is True
        _wait_until(lambda: not conn.execute(COUNT_BACKEND, params).scalar_one(), "backend sumir")


@dataclass
class _Killer:
    """Encerra o backend da conexão em uso, uma vez, antes do comando de `moment`."""

    killed_pid: int | None = None
    state_after_kill: _DbState | None = None


@contextmanager
def _backend_killed_before(
    engine: Engine, admin: Engine, moment: re.Pattern[str], read_state: Callable[[], _DbState]
) -> Iterator[_Killer]:
    """O backend morre antes do primeiro comando que casa com `moment`; os seguintes seguem.

    O pid vem do `before_cursor_execute`, dentro do comando da renovação: morre só a conexão em
    uso, nunca as demais (a da trava do teste, a de leitura do teste, as ociosas do pool). Logo
    depois do backend sumir, e antes de o cliente tentar de novo, `state_after_kill` guarda o que o
    banco mostra: é o estado em que a renovação interrompida deixou a Attempt e a Run.
    """
    killer = _Killer()

    def kill(
        conn: Connection, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        if not moment.search(statement):
            return
        pid = cast(Any, conn.connection.driver_connection).info.backend_pid
        if killer.killed_pid is None:
            killer.killed_pid = pid
            _terminate_and_wait(admin, pid)
            killer.state_after_kill = read_state()

    event.listen(engine, "before_cursor_execute", kill)
    try:
        yield killer
    finally:
        event.remove(engine, "before_cursor_execute", kill)


# A renovação, na ordem: trava da Run (`SELECT ... FOR UPDATE`), `UPDATE attempt` do lease e
# `UPDATE run` do progresso. Os dois últimos já estão dentro da transação que a trava abriu.
KILL_MOMENTS = [
    pytest.param(re.compile(r"\bFROM run\b.*\bFOR UPDATE\b", re.S), id="na-trava-da-run"),
    pytest.param(re.compile(r"\bUPDATE attempt\b"), id="no-update-do-lease"),
    pytest.param(re.compile(r"\bUPDATE run\b"), id="no-update-do-progresso"),
]


@pytest.mark.parametrize("moment", KILL_MOMENTS)
def test_backend_encerrado_na_renovacao_nao_abandona_o_executor_e_a_run_publica(
    factory: sessionmaker[Session],
    worker_engine: Engine,
    admin_engine: Engine,
    db_settings: Settings,
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
    moment: re.Pattern[str],
) -> None:
    scenario = _scenario(
        worker_engine, factory, admin_engine, db_settings, storage, work_root, caplog
    )
    killers: list[_Killer] = []

    @contextmanager
    def fault() -> Iterator[None]:
        with _backend_killed_before(
            worker_engine, admin_engine, moment, lambda: _state(scenario)
        ) as killer:
            killers.append(killer)
            yield

    outcome = _supervise_through(scenario, fault)

    [killer] = killers
    assert killer.killed_pid is not None
    assert killer.state_after_kill is not None
    _assert_lease_not_half_renewed(outcome.state_before, killer.state_after_kill)
    # Uma só falha: o backend morreu uma vez e a retentativa (`renew_retry_every`) já funcionou.
    [failure] = outcome.failures
    assert _fields(failure)["error_type"] == "OperationalError"
    assert _fields(failure)["sqlstate"] == ADMIN_SHUTDOWN
    _assert_failure_fields(scenario, outcome, "OperationalError", ADMIN_SHUTDOWN)
    _assert_survived_and_published(scenario, outcome)


# statement_timeout ------------------------------------------------------------------------


@contextmanager
def _run_locked_by_other_session(
    factory: sessionmaker[Session], run_id: uuid.UUID
) -> Iterator[None]:
    with factory() as session, session.begin():
        session.execute(select(run.c.id).where(run.c.id == run_id).with_for_update())
        yield


def test_statement_timeout_na_espera_da_trava_nao_abandona_o_executor_e_a_run_publica(
    factory: sessionmaker[Session],
    statement_timeout_engine: Engine,
    admin_engine: Engine,
    db_settings: Settings,
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenario = _scenario(
        statement_timeout_engine, factory, admin_engine, db_settings, storage, work_root, caplog
    )

    outcome = _supervise_through(
        scenario, lambda: _run_locked_by_other_session(factory, scenario.run_id)
    )

    # `lock_timeout` é de 60 s: quem cancela a espera é o `statement_timeout`, e vem como
    # `OperationalError` (`QueryCanceled`), o mesmo tipo da trava curta (55P03).
    _assert_lease_not_half_renewed(outcome.state_before, outcome.state_during_failure)
    _assert_failure_fields(scenario, outcome, "OperationalError", QUERY_CANCELED)
    _assert_survived_and_published(scenario, outcome)


# Pool esgotado ----------------------------------------------------------------------------


@contextmanager
def _pool_held(engine: Engine) -> Iterator[None]:
    """A única conexão do pool do worker, ocupada por outra sessão."""
    with Session(engine) as holder:
        holder.connection()
        yield


def test_pool_esgotado_na_renovacao_nao_abandona_o_executor_e_a_run_publica(
    factory: sessionmaker[Session],
    single_connection_engine: Engine,
    admin_engine: Engine,
    db_settings: Settings,
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenario = _scenario(
        single_connection_engine, factory, admin_engine, db_settings, storage, work_root, caplog
    )

    outcome = _supervise_through(scenario, lambda: _pool_held(single_connection_engine))

    # Não é erro do banco: o pool desiste antes de pedir conexão, e não há SQLSTATE.
    _assert_lease_not_half_renewed(outcome.state_before, outcome.state_during_failure)
    _assert_failure_fields(scenario, outcome, "TimeoutError", None)
    _assert_survived_and_published(scenario, outcome)
