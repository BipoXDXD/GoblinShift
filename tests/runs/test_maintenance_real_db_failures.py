"""Manutenção diante de falhas reais do PostgreSQL (levantamento L14-b).

`test_maintenance_steps.py` classifica erros fabricados (`DBAPIError.instance`). Aqui o banco
falha de verdade: backend encerrado por `pg_terminate_backend`, `statement_timeout` numa espera de
trava e pool esgotado. Os testes caracterizam o que a manutenção faz hoje com a forma que
psycopg e SQLAlchemy entregam: o passo conta em `steps_failed` e vai ao log com tipo e SQLSTATE; só
a perda de conexão pula os passos de banco restantes; o ciclo seguinte recomeça do banco.

A falha é armada em volta de um passo só (`_around_step`), então não depende de tempo: a trava ou
a conexão ocupada existe exatamente enquanto o passo roda. O único tempo real são os timeouts
curtos que o próprio cenário exige (`statement_timeout`, `pool_timeout`).
"""

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.settings import Settings
from goblinshift.runs.maintenance import STEPS_FAILED, STEPS_SKIPPED, Maintenance
from tests.runs.worker_support import local_runner

pytestmark = pytest.mark.integration

MAINTENANCE_LOGGER = "goblinshift.runs.maintenance"
STEP_FAILED_MESSAGE = "passo da manutenção falhou"
SKIPPED_AFTER_CONNECTION_MESSAGE = "banco inalcançável; passos de banco pulados nesta volta"
CYCLE_EVENT_MESSAGE = "ciclo de manutenção"
ADMIN_SHUTDOWN = "57P01"
QUERY_CANCELED = "57014"
WAIT_S = 10.0
POLL_S = 0.01
# Curtos para o teste não esperar, e `lock_timeout` longo para que só o `statement_timeout` encerre
# a espera pela trava (55P03 não pode ser a causa do 57014).
STATEMENT_TIMEOUT_MS = 200
LONG_LOCK_TIMEOUT_MS = 60_000
POOL_TIMEOUT_S = 0.1

# Os passos do ciclo, na ordem, cada um com uma contagem que só ele devolve; o único que não usa
# o banco é a limpeza de temporários do storage.
STEP_COUNTS: dict[str, str] = {
    "reconcile_expired_leases": "leases_reconciled",
    "expire_queue": "queue_expired",
    "apply_deadlines": "deadlines",
    "release_orphan_holds": "holds_released",
    "discard_stale_candidates": "candidates_discarded",
    "sweep_work_areas": "work_areas_removed",
    "apply_retention": "objects_purged",
    "sweep_temporary_parts": "tmp_parts_removed",
    "mark_missing_objects": "objects_marked_missing",
    "measure": "runs_stuck_stopping",
}
STORAGE_ONLY_STEP = "sweep_temporary_parts"
# Captura a própria exceção e a conta em `missing_sweep_failed`, sem passar por `steps_failed`.
SELF_ISOLATED_STEP = "mark_missing_objects"
# Sem área de trabalho no disco (o caso destes testes), devolve 0 sem abrir conexão.
NO_WORK_AREAS_STEP = "sweep_work_areas"
DATABASE_STEPS = [name for name in STEP_COUNTS if name != STORAGE_ONLY_STEP]


@pytest.fixture
def admin_engine(engine: Engine, db_settings: Settings) -> Iterator[Engine]:
    """Conexão de fora do pool da manutenção, para encerrar backends e segurar travas."""
    admin = make_engine(db_settings)
    yield admin
    admin.dispose()


def _janitor(factory: sessionmaker[Session], storage: FileStorage, work_root: Path) -> Maintenance:
    return Maintenance(factory, storage, local_runner(work_root))


def _tuned_factory(db_settings: Settings, **settings: object) -> Iterator[sessionmaker[Session]]:
    """Fábrica sobre um engine próprio (o fixture `engine` já deixou o banco migrado e vazio)."""
    tuned = make_engine(db_settings.model_copy(update=settings))
    yield make_session_factory(tuned)
    tuned.dispose()


@pytest.fixture
def statement_timeout_factory(
    engine: Engine, db_settings: Settings
) -> Iterator[sessionmaker[Session]]:
    yield from _tuned_factory(
        db_settings,
        db_statement_timeout_ms=STATEMENT_TIMEOUT_MS,
        db_lock_timeout_ms=LONG_LOCK_TIMEOUT_MS,
    )


@pytest.fixture
def single_connection_factory(
    engine: Engine, db_settings: Settings
) -> Iterator[sessionmaker[Session]]:
    yield from _tuned_factory(
        db_settings, db_pool_size=1, db_pool_max_overflow=0, db_pool_timeout_s=POOL_TIMEOUT_S
    )


def _around_step(
    janitor: Maintenance,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    fault: Callable[[], AbstractContextManager[None]],
) -> None:
    """A falha existe só enquanto o passo `name` roda; os outros passos veem o banco são."""
    real = getattr(janitor, name)

    def wrapped(**kwargs: object) -> object:
        with fault():
            return real(**kwargs)

    monkeypatch.setattr(janitor, name, wrapped)


# Backends de cliente deste banco, fora o da própria consulta; com `:pid`, só aquele.
_TARGET_BACKENDS = (
    "FROM pg_stat_activity WHERE datname = current_database() AND backend_type = 'client backend'"
    " AND pid <> pg_backend_pid() AND (CAST(:pid AS integer) IS NULL OR pid = :pid)"
)
TERMINATE_BACKENDS = text(f"SELECT pg_terminate_backend(pid) {_TARGET_BACKENDS}")
COUNT_BACKENDS = text(f"SELECT count(*) {_TARGET_BACKENDS}")


def _terminate_and_wait(admin: Engine, only_pid: int | None = None) -> None:
    """`pg_terminate_backend` de um backend (ou de todos os outros do banco) até sumirem.

    O sinal é assíncrono; esperar o backend sair de `pg_stat_activity` tira a corrida entre a
    morte dele e o próximo comando do cliente. AUTOCOMMIT: o snapshot das estatísticas vale por
    transação, e uma transação aberta repetiria sempre a mesma resposta.
    """
    params = {"pid": only_pid}
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(TERMINATE_BACKENDS, params)
        deadline = time.monotonic() + WAIT_S
        while conn.execute(COUNT_BACKENDS, params).scalar_one():
            assert time.monotonic() < deadline, "backend não terminou a tempo"
            threading.Event().wait(POLL_S)


@contextmanager
def _backend_killed_before_statement(
    engine: Engine, admin: Engine, statements_before: int
) -> Iterator[None]:
    """Encerra o backend da conexão em uso antes do comando de número `statements_before`.

    Conta os comandos do passo (0 = o primeiro, antes de qualquer resposta do banco).
    """
    seen = 0

    def kill(
        conn: Connection, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        nonlocal seen
        if seen == statements_before:
            pid = cast(Any, conn.connection.driver_connection).info.backend_pid
            _terminate_and_wait(admin, only_pid=pid)
        seen += 1

    event.listen(engine, "before_cursor_execute", kill)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", kill)


@contextmanager
def _table_locked_by_other_session(admin: Engine, table: str) -> Iterator[None]:
    """Outra sessão com `ACCESS EXCLUSIVE` na tabela: qualquer leitura dela espera."""
    with admin.connect() as conn:
        conn.execute(text(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE NOWAIT"))
        try:
            yield
        finally:
            conn.rollback()


def _step_failures(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == STEP_FAILED_MESSAGE]


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    return cast(dict[str, Any], record.fields)  # type: ignore[attr-defined]


def _steps_that_ran(counts: dict[str, int]) -> list[str]:
    return [name for name, key in STEP_COUNTS.items() if key in counts]


def _cycle_event(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    [record] = [r for r in caplog.records if r.getMessage() == CYCLE_EVENT_MESSAGE]
    return _fields(record)


def _assert_healthy_cycle(janitor: Maintenance, baseline: dict[str, int]) -> None:
    """O ciclo seguinte, sem a falha, roda inteiro e devolve o mesmo que o ciclo são de antes."""
    assert janitor.run_cycle() == baseline


# Backend encerrado ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failing", "statements_before"),
    [("expire_queue", 0), ("measure", 1)],
    ids=["antes_do_primeiro_comando_do_passo", "no_meio_da_transacao_do_ultimo_passo"],
)
def test_backend_encerrado_no_passo_conta_falha_e_pula_so_os_passos_de_banco_seguintes(
    factory: sessionmaker[Session],
    engine: Engine,
    admin_engine: Engine,
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failing: str,
    statements_before: int,
) -> None:
    janitor = _janitor(factory, storage, work_root)
    baseline = janitor.run_cycle()
    _around_step(
        janitor,
        monkeypatch,
        failing,
        lambda: _backend_killed_before_statement(engine, admin_engine, statements_before),
    )
    caplog.clear()

    with caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER):
        counts = janitor.run_cycle()

    position = DATABASE_STEPS.index(failing)
    skipped = DATABASE_STEPS[position + 1 :]
    assert (counts[STEPS_FAILED], counts[STEPS_SKIPPED]) == (1, len(skipped))
    # Rodam os passos anteriores e o do storage (que não usa banco); o que falhou e os de banco
    # seguintes ficam fora das contagens.
    failing_index = list(STEP_COUNTS).index(failing)
    assert _steps_that_ran(counts) == [
        name for i, name in enumerate(STEP_COUNTS) if i < failing_index or name == STORAGE_ONLY_STEP
    ]
    [failure] = _step_failures(caplog)
    assert _fields(failure) == {
        "step": failing,
        "error_type": "OperationalError",
        "sqlstate": ADMIN_SHUTDOWN,
    }
    error = cast(DBAPIError, failure.exc_info[1] if failure.exc_info else None)
    assert error.connection_invalidated
    if skipped:
        [notice] = [r for r in caplog.records if r.getMessage() == SKIPPED_AFTER_CONNECTION_MESSAGE]
        assert _fields(notice) == {"failed_step": failing, "skipped": skipped}
    else:
        assert SKIPPED_AFTER_CONNECTION_MESSAGE not in caplog.text
    event_fields = _cycle_event(caplog)
    assert (event_fields["steps_failed"], event_fields["steps_skipped"]) == (1, len(skipped))
    monkeypatch.undo()
    _assert_healthy_cycle(janitor, baseline)


def test_backend_encerrado_com_a_conexao_ociosa_no_pool_nao_falha_o_ciclo(
    factory: sessionmaker[Session],
    admin_engine: Engine,
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    janitor = _janitor(factory, storage, work_root)
    baseline = janitor.run_cycle()
    # O pool guarda a conexão do ciclo anterior; o banco a derruba entre dois ciclos (reinício do
    # servidor, failover, `idle_session_timeout`). O `pool_pre_ping` troca a conexão no checkout.
    _terminate_and_wait(admin_engine)

    with caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER):
        counts = janitor.run_cycle()

    assert counts == baseline
    assert not _step_failures(caplog)


# statement_timeout ------------------------------------------------------------------------


def test_statement_timeout_numa_espera_de_trava_falha_so_o_passo_e_nao_pula_os_outros(
    statement_timeout_factory: sessionmaker[Session],
    admin_engine: Engine,
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    janitor = _janitor(statement_timeout_factory, storage, work_root)
    baseline = janitor.run_cycle()
    _around_step(
        janitor,
        monkeypatch,
        "apply_deadlines",
        lambda: _table_locked_by_other_session(admin_engine, "run"),
    )
    caplog.clear()

    with caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER):
        counts = janitor.run_cycle()

    assert (counts[STEPS_FAILED], counts[STEPS_SKIPPED]) == (1, 0)
    assert _steps_that_ran(counts) == [n for n in STEP_COUNTS if n != "apply_deadlines"]
    [failure] = _step_failures(caplog)
    assert _fields(failure) == {
        "step": "apply_deadlines",
        "error_type": "OperationalError",
        "sqlstate": QUERY_CANCELED,
    }
    # O banco respondeu: a conexão segue válida e nada é pulado nem registrado como perda.
    error = cast(DBAPIError, failure.exc_info[1] if failure.exc_info else None)
    assert not error.connection_invalidated
    assert SKIPPED_AFTER_CONNECTION_MESSAGE not in caplog.text
    assert _cycle_event(caplog)["steps_failed"] == 1
    monkeypatch.undo()
    _assert_healthy_cycle(janitor, baseline)


# Pool esgotado ----------------------------------------------------------------------------


def test_pool_esgotado_durante_um_passo_falha_so_esse_passo(
    single_connection_factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    janitor = _janitor(single_connection_factory, storage, work_root)
    baseline = janitor.run_cycle()

    @contextmanager
    def pool_held() -> Iterator[None]:
        with single_connection_factory() as holder:
            holder.connection()  # a única conexão do pool, ocupada por outra sessão
            yield

    _around_step(janitor, monkeypatch, "apply_deadlines", pool_held)
    caplog.clear()

    with caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER):
        counts = janitor.run_cycle()

    assert (counts[STEPS_FAILED], counts[STEPS_SKIPPED]) == (1, 0)
    assert _steps_that_ran(counts) == [n for n in STEP_COUNTS if n != "apply_deadlines"]
    [failure] = _step_failures(caplog)
    # Não é erro do banco: o pool desiste antes de pedir conexão, e não há SQLSTATE.
    assert _fields(failure) == {
        "step": "apply_deadlines",
        "error_type": "TimeoutError",
        "sqlstate": None,
    }
    monkeypatch.undo()
    _assert_healthy_cycle(janitor, baseline)


def test_pool_esgotado_o_ciclo_todo_hoje_nao_pula_passos_e_cada_um_espera_o_pool_timeout(
    single_connection_factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Caracteriza o comportamento atual: `TimeoutError` do pool não é "conexão perdida".

    O ciclo não levanta e o storage roda, mas cada passo de banco espera `pool_timeout` de novo
    (`_is_connection_error` só reconhece `DBAPIError`). O custo é limitado: passos x timeout.
    A varredura de ausentes se isola sozinha: a falha dela vai a `missing_sweep_failed`, não a
    `steps_failed`; e a limpeza de áreas de trabalho nem abre conexão com o disco vazio.
    """
    janitor = _janitor(single_connection_factory, storage, work_root)
    baseline = janitor.run_cycle()
    caplog.clear()

    with (
        caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER),
        single_connection_factory() as holder,
    ):
        holder.connection()
        counts = janitor.run_cycle()

    raising = [
        name for name in DATABASE_STEPS if name not in {SELF_ISOLATED_STEP, NO_WORK_AREAS_STEP}
    ]
    assert (counts[STEPS_FAILED], counts[STEPS_SKIPPED]) == (len(raising), 0)
    assert counts["missing_sweep_failed"] == 1
    assert _steps_that_ran(counts) == [NO_WORK_AREAS_STEP, STORAGE_ONLY_STEP, SELF_ISOLATED_STEP]
    failures = [_fields(r) for r in _step_failures(caplog)]
    assert [f["step"] for f in failures] == raising
    assert {(f["error_type"], f["sqlstate"]) for f in failures} == {("TimeoutError", None)}
    assert SKIPPED_AFTER_CONNECTION_MESSAGE not in caplog.text
    _assert_healthy_cycle(janitor, baseline)


def test_backend_encerrado_na_varredura_de_ausentes_e_isolado_nela_e_o_ciclo_segue(
    factory: sessionmaker[Session],
    engine: Engine,
    admin_engine: Engine,
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A varredura captura o erro de conexão sozinha: o ciclo não o vê como perda de conexão.

    Hoje isso é inofensivo (o único passo depois dela, `measure`, abre uma conexão nova), mas a
    perda de conexão não chega a `_CycleOutcome`: com o banco fora de verdade, `measure` esperaria
    o `connect_timeout` uma vez a mais.
    """
    janitor = _janitor(factory, storage, work_root)
    baseline = janitor.run_cycle()
    _around_step(
        janitor,
        monkeypatch,
        SELF_ISOLATED_STEP,
        lambda: _backend_killed_before_statement(engine, admin_engine, 0),
    )
    caplog.clear()

    with caplog.at_level(logging.INFO, logger=MAINTENANCE_LOGGER):
        counts = janitor.run_cycle()

    assert (counts[STEPS_FAILED], counts[STEPS_SKIPPED]) == (0, 0)
    assert counts["missing_sweep_failed"] == 1
    assert "runs_stuck_stopping" in counts
    [failure] = [r for r in caplog.records if r.getMessage() == "varredura de ausentes falhou"]
    assert _fields(failure) == {"alert": "missing_sweep_failed"}
    error = cast(DBAPIError, failure.exc_info[1] if failure.exc_info else None)
    assert (error.connection_invalidated, getattr(error.orig, "sqlstate", None)) == (
        True,
        ADMIN_SHUTDOWN,
    )
    assert not _step_failures(caplog)
    monkeypatch.undo()
    _assert_healthy_cycle(janitor, baseline)
