"""Worker de ponta a ponta com PostgreSQL e executor reais (docs/GoblinShiftPlan.md, 3.4 e 3.5)."""

import threading
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.states import ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, stored_object
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import LOCAL_EXECUTOR_IMAGE
from goblinshift.runs.acceptance import accept_run
from goblinshift.runs.cancellation import cancel_run
from goblinshift.runs.store import complete_after_commit
from goblinshift.runs.tables import run, run_input, run_output
from goblinshift.runs.worker.claim import claim_next
from goblinshift.runs.worker.collection import collect_outputs
from goblinshift.runs.worker.runner import ExecutorPhase
from tests.runs.support import new_key, ready_inputs, run_command
from tests.runs.worker_support import (
    INVALID,
    LEASE,
    VALID,
    attempts,
    claim,
    coordinator,
    create_user,
    local_runner,
    make_eligible_now,
    open_run_uses,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration


def outputs_of(factory: sessionmaker[Session], run_id: uuid.UUID) -> list[tuple[str, str]]:
    with factory() as session:
        rows = session.execute(
            select(run_output.c.logical_name, artifact.c.role)
            .join(artifact, artifact.c.id == run_output.c.artifact_id)
            .where(run_output.c.run_id == run_id)
            .order_by(run_output.c.position)
        ).all()
    return [(row.logical_name, row.role) for row in rows]


def input_outcomes(
    factory: sessionmaker[Session], run_id: uuid.UUID
) -> list[tuple[str | None, str | None]]:
    with factory() as session:
        rows = session.execute(
            select(run_input.c.outcome, run_input.c.error_code)
            .where(run_input.c.run_id == run_id)
            .order_by(run_input.c.position)
        ).all()
    return [(row.outcome, row.error_code) for row in rows]


def test_fluxo_feliz_publica_saidas_e_relatorio(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root)
    claimed = claim(factory, storage)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["error_code"]) == ("succeeded", None, None)
    assert row["finished_at"] is not None
    assert row["summary"] == {"inputs": 1, "ok": 1, "rejected": 0, "outputs": 1}
    assert outputs_of(factory, run_id) == [("clientes.csv", "output"), ("report.json", "report")]
    assert input_outcomes(factory, run_id) == [("ok", None)]
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is not None
    assert only["executor_started_at"] is not None
    assert only["error_code"] is None
    assert open_run_uses(factory, run_id) == 0
    assert runner.status(claimed.attempt_id).phase is ExecutorPhase.ABSENT
    assert not runner.work_dir(claimed.attempt_id).exists()


def test_artefato_publicado_tem_o_conteudo_limpo_na_chave_da_geracao(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claimed = claim(factory, storage)

    coordinator(factory, storage, local_runner(work_root)).run(claimed)

    with factory() as session:
        key = session.execute(
            select(stored_object.c.storage_key)
            .join(artifact, artifact.c.object_id == stored_object.c.id)
            .join(run_output, run_output.c.artifact_id == artifact.c.id)
            .where(run_output.c.run_id == run_id, run_output.c.logical_name == "clientes.csv")
        ).scalar_one()
    assert key.startswith(f"runs/{run_id}/g1/")
    with storage.open(key) as content:
        assert content.read() == VALID


def test_tabela_rejeitada_termina_com_avisos(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(
        factory, storage, create_user(factory), [("a.csv", VALID), ("b.csv", INVALID)]
    )

    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))

    assert run_row(factory, run_id)["status"] == "succeeded_with_warnings"
    assert input_outcomes(factory, run_id) == [("ok", None), ("rejected", "CSV_STRUCTURE_INVALID")]
    assert outputs_of(factory, run_id) == [("a.csv", "output"), ("report.json", "report")]


def test_todas_rejeitadas_termina_failed_sem_saidas_validas(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory), [("b.csv", INVALID)])

    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("failed", "NO_VALID_OUTPUTS")
    assert outputs_of(factory, run_id) == [("report.json", "report")]
    assert input_outcomes(factory, run_id) == [("rejected", "CSV_STRUCTURE_INVALID")]
    assert open_run_uses(factory, run_id) == 0


def test_dois_workers_disputando_o_mesmo_claim_so_um_ganha(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    barrier = threading.Barrier(2)
    results: list[object] = []

    def worker() -> None:
        barrier.wait()
        results.append(claim_next(factory, storage, LEASE))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(result is None for result in results) == [False, True]
    row = run_row(factory, run_id)
    assert (row["generation"], row["claim_count"]) == (1, 1)
    assert len(attempts(factory, run_id)) == 1


def test_claim_pula_a_linha_travada_por_outro_worker(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    with factory() as holder, holder.begin():
        holder.execute(select(run.c.id).where(run.c.id == run_id).with_for_update())

        assert claim_next(factory, storage, LEASE) is None

    assert claim_next(factory, storage, LEASE) is not None


def test_claim_fixa_prazo_e_lease_pelo_relogio_do_banco(
    factory: sessionmaker[Session], storage: FileStorage
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))

    claimed = claim(factory, storage)

    with factory() as session:
        now = session.execute(select(func.now())).scalar_one()
    row = run_row(factory, run_id)
    [only] = attempts(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "inspecting")
    assert timedelta(minutes=29) < row["deadline_at"] - now <= timedelta(minutes=30)
    assert timedelta(0) < only["lease_expires_at"] - now <= LEASE
    assert only["container_name"] == f"gs-exec-{claimed.attempt_id}"
    assert (only["number"], only["generation"]) == (1, 1)


def test_falha_deterministica_termina_failed_com_o_codigo_e_sem_retry(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root, "fail-deterministic", "OUTPUT_LIMIT_EXCEEDED")

    coordinator(factory, storage, runner).run(claim(factory, storage))

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("failed", "OUTPUT_LIMIT_EXCEEDED")
    assert outputs_of(factory, run_id) == []
    assert open_run_uses(factory, run_id) == 0


def test_falha_transitoria_reagenda_e_a_segunda_tentativa_publica(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path, tmp_path: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    runner = local_runner(work_root, "fail-once", str(tmp_path / "falhou"))
    worker = coordinator(factory, storage, runner)

    worker.run(claim(factory, storage))
    waiting = run_row(factory, run_id)
    make_eligible_now(factory, run_id)
    second = claim(factory, storage)
    worker.run(second)

    assert (waiting["status"], waiting["phase"]) == ("running", "retry_wait")
    assert second.generation == 2
    assert run_row(factory, run_id)["status"] == "succeeded"
    first, last = attempts(factory, run_id)
    assert first["error_code"] == "EXECUTOR_UNAVAILABLE"
    assert first["executor_started_at"] is not None
    assert last["error_code"] is None


def test_segunda_falha_fisica_termina_failed(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    worker = coordinator(factory, storage, local_runner(work_root, "exit", "4"))

    worker.run(claim(factory, storage))
    make_eligible_now(factory, run_id)
    worker.run(claim(factory, storage))

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"], row["stop_reason"]) == (
        "failed",
        "EXECUTOR_UNAVAILABLE",
        "EXECUTOR_UNAVAILABLE",
    )
    assert [a["executor_started_at"] is not None for a in attempts(factory, run_id)] == [True, True]
    assert claim_next(factory, storage, LEASE) is None
    assert open_run_uses(factory, run_id) == 0


def test_publicacao_contra_cancelamento_nao_publica_e_descarta_candidatos(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    runner = local_runner(work_root)
    worker = coordinator(factory, storage, runner)
    claimed = claim(factory, storage)
    worker.prepare(claimed)
    runner.start(claimed.attempt_id, claimed.executor_image)
    worker.record_start(claimed)
    status = worker.supervise(claimed)
    collected = collect_outputs(runner.work_dir(claimed.attempt_id) / "out", 1)
    stored = worker.store_candidates(claimed, collected)

    cancel_run(factory, storage, owner, run_id)
    applied = worker.publish(claimed, collected, stored)

    assert status.exit_code == 0
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("canceled", "CANCELED")
    assert applied.state.status == "canceled"
    assert outputs_of(factory, run_id) == []
    with factory() as session:
        states = session.execute(
            select(stored_object.c.state).where(stored_object.c.attempt_id == claimed.attempt_id)
        ).scalars()
        assert set(states) == {ObjectState.DELETE_REQUESTED}


def test_publicacao_depois_do_prazo_termina_failed_pelo_relogio_do_banco(
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

    with unit_of_work(factory) as session:
        session.execute(
            update(run)
            .where(run.c.id == run_id)
            .values(deadline_at=func.now() - timedelta(seconds=1))
        )
    complete_after_commit(factory, storage, worker.publish(claimed, collected, stored))

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("failed", "DEADLINE_EXCEEDED")
    assert outputs_of(factory, run_id) == []
    assert open_run_uses(factory, run_id) == 0


def test_execucao_aceita_pela_api_e_processada_pelo_worker(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    [upload_id] = ready_inputs(factory, storage, owner)
    with factory() as session:
        accepted = accept_run(
            session,
            owner,
            run_command([upload_id]),
            new_key(),
            None,
            executor_image=LOCAL_EXECUTOR_IMAGE,
        )

    coordinator(factory, storage, local_runner(work_root)).run(claim(factory, storage))

    row = run_row(factory, accepted.run_id)
    assert row["status"] == "succeeded"
    assert outputs_of(factory, accepted.run_id)[-1] == ("report.json", "report")
    assert open_run_uses(factory, accepted.run_id) == 0
