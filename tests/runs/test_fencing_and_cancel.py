"""Fencing de cada escrita do coordenador e cancelamento (3.4, 3.5 e 3.7 do plano)."""

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.cancellation import RunNotFoundError, cancel_run
from goblinshift.runs.lifecycle import Phase, Status
from goblinshift.runs.store import LostFencing, release_run_holds, run_transaction
from goblinshift.runs.tables import attempt, run
from goblinshift.runs.worker.collection import collect_outputs
from tests.runs.worker_support import (
    attempts,
    claim,
    coordinator,
    create_user,
    local_runner,
    open_run_uses,
    queue_run,
    run_row,
    snapshot,
)

pytestmark = pytest.mark.integration
WAIT_S = 20.0


def wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT_S
    while not condition():
        assert time.monotonic() < deadline, "condição não chegou a tempo"
        time.sleep(0.05)


def test_coordenador_com_geracao_vencida_nao_escreve_nada(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    worker = coordinator(factory, storage, runner)
    claimed = claim(factory, storage)
    worker.prepare(claimed)
    runner.start(claimed.attempt_id, claimed.executor_image)
    worker.record_start(claimed)
    worker.supervise(claimed)
    collected = collect_outputs(runner.work_dir(claimed.attempt_id) / "out", 1)
    stored = worker.store_candidates(claimed, collected)
    # Outra tentativa assumiu: a geração mudou depois que este coordenador leu a sua.
    with unit_of_work(factory) as session:
        session.execute(
            update(run).where(run.c.id == run_id).values(generation=run.c.generation + 1)
        )
    before = snapshot(factory)

    writes: list[Callable[[], object]] = [
        lambda: worker.renew_lease(claimed),
        lambda: worker.reach_phase(claimed, Phase.PACKAGING),
        lambda: worker.record_start(claimed),
        lambda: worker.store_candidates(claimed, collected),
        lambda: worker.publish(claimed, collected, stored),
        lambda: worker.fail_transiently(claimed),
        lambda: worker.conclude_stop(claimed),
        lambda: run_transaction(
            factory, lambda s: release_run_holds(s, run_id, claimed.generation)
        ),
    ]
    for write in writes:
        with pytest.raises(LostFencing):
            write()

    assert snapshot(factory) == before


def test_coordenador_cuja_tentativa_terminou_nao_escreve_nada(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    worker = coordinator(factory, storage, local_runner(work_root))
    claimed = claim(factory, storage)
    # Mesma geração, mas a Attempt foi encerrada pelo reconciliador (lease vencido).
    with unit_of_work(factory) as session:
        session.execute(
            update(attempt).where(attempt.c.run_id == run_id).values(ended_at=attempt.c.started_at)
        )
        session.execute(update(run).where(run.c.id == run_id).values(phase="retry_wait"))
    before = snapshot(factory)

    for write in (
        lambda: worker.renew_lease(claimed),
        lambda: worker.record_start(claimed),
        lambda: worker.fail_transiently(claimed),
    ):
        with pytest.raises(LostFencing):
            write()

    assert snapshot(factory) == before


def test_run_com_fencing_perdido_devolve_none(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)
    with unit_of_work(factory) as session:
        session.execute(update(run).where(run.c.id == run_id).values(generation=7))

    assert coordinator(factory, storage, local_runner(work_root)).run(claimed) is None
    assert run_row(factory, run_id)["status"] == "running"


def test_cancelar_execucao_na_fila_termina_na_hora_e_libera_usos(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)

    result = cancel_run(factory, storage, owner, run_id)

    assert (result.status, result.error_code, result.is_terminal) == (
        Status.CANCELED,
        "CANCELED",
        True,
    )
    row = run_row(factory, run_id)
    assert row["cancel_requested_at"] is not None
    assert row["finished_at"] is not None
    assert attempts(factory, run_id) == []
    assert open_run_uses(factory, run_id) == 0


def test_cancelar_execucao_rodando_para_o_executor_antes_de_confirmar(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    runner = local_runner(work_root, "sleep", "30")
    claimed = claim(factory, storage)
    thread = threading.Thread(target=coordinator(factory, storage, runner).run, args=(claimed,))
    thread.start()
    wait_until(lambda: attempts(factory, run_id)[0]["executor_started_at"] is not None)

    result = cancel_run(factory, storage, owner, run_id)
    wait_until(lambda: run_row(factory, run_id)["status"] == "canceled")
    stopped = runner.status(claimed.attempt_id)
    thread.join(timeout=WAIT_S)

    assert (result.status, result.is_terminal) == (Status.STOPPING, False)
    assert not stopped.is_running
    row = run_row(factory, run_id)
    assert (row["error_code"], row["stop_reason"]) == ("CANCELED", "CANCELED")
    assert open_run_uses(factory, run_id) == 0


def test_cancelar_execucao_terminada_devolve_o_estado_real(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))

    result = cancel_run(factory, storage, owner, run_id)

    assert (result.status, result.is_terminal) == (Status.SUCCEEDED, True)
    assert run_row(factory, run_id)["cancel_requested_at"] is None


def test_cancelar_execucao_de_outra_conta_nao_encontra(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    run_id = queue_run(factory, storage, create_user(factory, "ana"))
    other = create_user(factory, "bia")

    with pytest.raises(RunNotFoundError):
        cancel_run(factory, storage, other, run_id)
    with factory() as session:
        status = session.execute(select(run.c.status).where(run.c.id == run_id)).scalar_one()
    assert status == "queued"
