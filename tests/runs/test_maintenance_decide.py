"""Decisão da manutenção sob concorrência e a parada confirmada na hora, com PostgreSQL real.

`Maintenance._decide` relê a Run com `SKIP LOCKED` e só grava se a linha está livre, se ainda
há evento e se a tabela de 3.5 aceita a transição. Quando a transição pede a parada de um
executor que a reconciliação já confirmou parado, `_confirm_stopped` termina a Run na mesma
transação; se essa confirmação falha, nada da transação fica gravado.
"""

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.lifecycle import (
    MIN_RETRY_JITTER,
    Event,
    ExecutorStopped,
    RefusalReason,
    Refused,
    RunState,
    Transition,
    decide_transition,
)
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from goblinshift.runs.store import LoadedRun, load_run
from goblinshift.runs.tables import attempt, run
from goblinshift.runs.worker.runner import ExecutorStatus, LocalRunner
from tests.runs.worker_support import (
    attempts,
    claim,
    create_user,
    expire_lease,
    local_runner,
    make_eligible_now,
    open_run_uses,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

SKIP_LOCKED = "SKIP LOCKED"
MAINTENANCE_MODULE = "goblinshift.runs.maintenance"


def _janitor(
    factory: sessionmaker[Session], storage: FileStorage, runner: LocalRunner
) -> Maintenance:
    config = MaintenanceConfig(stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER)
    return Maintenance(factory, storage, runner, config)


def _set_run(factory: sessionmaker[Session], run_id: uuid.UUID, **values: object) -> None:
    with unit_of_work(factory) as session:
        session.execute(update(run).where(run.c.id == run_id).values(**values))


def _mark_executor_started(factory: sessionmaker[Session], run_id: uuid.UUID) -> None:
    """O worker registrou o início do executor da tentativa viva e depois sumiu."""
    with unit_of_work(factory) as session:
        session.execute(
            update(attempt)
            .where(attempt.c.run_id == run_id, attempt.c.ended_at.is_(None))
            .values(executor_started_at=func.now())
        )


def _expired_queue_run(factory: sessionmaker[Session], storage: FileStorage) -> uuid.UUID:
    run_id = queue_run(factory, storage, create_user(factory))
    _set_run(factory, run_id, queue_expires_at=func.now() - timedelta(seconds=1))
    return run_id


@contextmanager
def _row_locked_by_other_transaction(engine: Engine, run_id: uuid.UUID) -> Iterator[None]:
    with engine.connect() as other, other.begin():
        other.execute(select(run.c.id).where(run.c.id == run_id).with_for_update())
        yield


@contextmanager
def _before_decision(engine: Engine, action: Callable[[], None]) -> Iterator[None]:
    """Roda `action` em outra conexão logo antes da releitura com `SKIP LOCKED`, uma vez."""
    done: list[bool] = []

    def before(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if SKIP_LOCKED in statement and not done:
            done.append(True)
            action()

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", before)


def _stop_requested_on_reconcile(factory: sessionmaker[Session], storage: FileStorage) -> uuid.UUID:
    """Tentativa cujo executor rodou, com o prazo vencido e o worker sumido."""
    run_id = queue_run(factory, storage, create_user(factory))
    claim(factory, storage)
    _mark_executor_started(factory, run_id)
    _set_run(factory, run_id, deadline_at=func.now() - timedelta(seconds=1))
    expire_lease(factory, run_id)
    return run_id


# Linha ocupada, evento sumido e transição recusada --------------------------------------


def test_run_travada_por_outra_transacao_fica_para_o_ciclo_seguinte(
    engine: Engine, factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = _expired_queue_run(factory, storage)
    janitor = _janitor(factory, storage, local_runner(work_root))

    with _row_locked_by_other_transaction(engine, run_id):
        expired_while_locked = janitor.expire_queue()

    assert expired_while_locked == 0
    assert run_row(factory, run_id)["status"] == "queued"
    assert open_run_uses(factory, run_id) == 1
    # Liberada a trava, o ciclo seguinte decide a mesma Run.
    assert janitor.expire_queue() == 1
    assert run_row(factory, run_id)["error_code"] == "QUEUE_EXPIRED"


def test_lease_renovado_antes_da_decisao_nao_e_reconciliado(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claim(factory, storage)
    expire_lease(factory, run_id)
    runner = local_runner(work_root)
    original_status = runner.status
    removed: list[uuid.UUID] = []

    def status_then_renew(attempt_id: uuid.UUID) -> ExecutorStatus:
        # O coordenador volta e renova o lease entre a consulta ao executor e a decisão.
        status = original_status(attempt_id)
        with unit_of_work(factory) as session:
            session.execute(
                update(attempt)
                .where(attempt.c.id == attempt_id)
                .values(lease_expires_at=func.now() + timedelta(minutes=5))
            )
        return status

    monkeypatch.setattr(runner, "status", status_then_renew)
    monkeypatch.setattr(runner, "remove", removed.append)

    reconciled = _janitor(factory, storage, runner).reconcile_expired_leases()

    assert reconciled == 0
    assert removed == []
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["generation"]) == ("running", "inspecting", 1)
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is None


def test_transicao_recusada_na_releitura_nao_grava_nada(
    engine: Engine, factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = _expired_queue_run(factory, storage)
    janitor = _janitor(factory, storage, local_runner(work_root))

    def extend_queue_window() -> None:
        # Entre a seleção e a decisão a fila ganha prazo: QueueExpired passa a ser NOT_YET_DUE.
        with engine.begin() as other:
            other.execute(
                update(run)
                .where(run.c.id == run_id)
                .values(queue_expires_at=func.now() + timedelta(hours=1))
            )

    with _before_decision(engine, extend_queue_window):
        expired = janitor.expire_queue()

    assert expired == 0
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("queued", None)
    assert open_run_uses(factory, run_id) == 1


# Parada pedida e confirmada na mesma transação ------------------------------------------


@pytest.mark.parametrize(
    ("deadline_passed", "expected_error"),
    [
        pytest.param(True, "DEADLINE_EXCEEDED", id="prazo-vencido"),
        pytest.param(False, "EXECUTOR_UNAVAILABLE", id="tentativas-esgotadas"),
    ],
)
def test_parada_pedida_na_reconciliacao_termina_a_run_no_mesmo_ciclo(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    deadline_passed: bool,
    expected_error: str,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    janitor = _janitor(factory, storage, local_runner(work_root))
    if not deadline_passed:
        # Primeira tentativa física gasta: a segunda é a última (MAX_ATTEMPTS = 2).
        claim(factory, storage)
        _mark_executor_started(factory, run_id)
        expire_lease(factory, run_id)
        assert janitor.reconcile_expired_leases() == 1
        make_eligible_now(factory, run_id)
    claim(factory, storage)
    _mark_executor_started(factory, run_id)
    if deadline_passed:
        _set_run(factory, run_id, deadline_at=func.now() - timedelta(seconds=1))
    expire_lease(factory, run_id)

    reconciled = janitor.reconcile_expired_leases()

    assert reconciled == 1
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"], row["stop_reason"]) == (
        "failed",
        expected_error,
        expected_error,
    )
    last = attempts(factory, run_id)[-1]
    assert last["ended_at"] is not None
    assert last["error_code"] == expected_error
    assert open_run_uses(factory, run_id) == 0


def test_primeira_tentativa_fisica_perdida_volta_para_retry_wait_sem_parada(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claim(factory, storage)
    _mark_executor_started(factory, run_id)
    expire_lease(factory, run_id)

    reconciled = _janitor(factory, storage, local_runner(work_root)).reconcile_expired_leases()

    assert reconciled == 1
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["stop_reason"]) == ("running", "retry_wait", None)
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is not None
    assert open_run_uses(factory, run_id) == 1


def _refuse_executor_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def refusing(state: RunState, event: Event) -> Transition | Refused:
        if isinstance(event, ExecutorStopped):
            return Refused(RefusalReason.STALE_GENERATION)
        return decide_transition(state, event)

    monkeypatch.setattr(f"{MAINTENANCE_MODULE}.decide_transition", refusing)


def _lose_run_on_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:

    def losing(session: Session, run_id: uuid.UUID, skip_locked: bool = False) -> LoadedRun | None:
        # Só a releitura da confirmação (sem SKIP LOCKED) não acha a Run.
        return load_run(session, run_id, skip_locked=True) if skip_locked else None

    monkeypatch.setattr(f"{MAINTENANCE_MODULE}.load_run", losing)


@pytest.mark.parametrize(
    "break_confirmation",
    [
        pytest.param(_refuse_executor_stopped, id="confirmacao-recusada"),
        pytest.param(_lose_run_on_confirmation, id="run-sumiu-na-confirmacao"),
    ],
)
def test_confirmacao_da_parada_que_falha_desfaz_a_transicao_inteira(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    break_confirmation: Callable[[pytest.MonkeyPatch], None],
) -> None:
    run_id = _stop_requested_on_reconcile(factory, storage)
    janitor = _janitor(factory, storage, local_runner(work_root))

    with monkeypatch.context() as patch:
        break_confirmation(patch)
        reconciled = janitor.reconcile_expired_leases()

    assert reconciled == 0
    # Nem o `stopping` da primeira transição ficou: a Run segue como o worker a deixou.
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["stop_reason"]) == ("running", "inspecting", None)
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is None
    assert open_run_uses(factory, run_id) == 1
    # O ciclo seguinte, sem a falha, conclui.
    assert janitor.reconcile_expired_leases() == 1
    assert run_row(factory, run_id)["error_code"] == "DEADLINE_EXCEEDED"
