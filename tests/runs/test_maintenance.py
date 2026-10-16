"""Reconciliação da manutenção com PostgreSQL real (docs/GoblinShiftPlan.md, 3.4 e 3.5)."""

import logging
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import commit_candidate, create_candidate_object
from goblinshift.files.missing import GOVERNOR_MIN_SAMPLE
from goblinshift.files.states import Availability, ObjectState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import stored_object, upload
from goblinshift.files.uploads import get_upload
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.cancellation import cancel_run
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from goblinshift.runs.tables import run
from goblinshift.runs.worker.claim import claim_next
from goblinshift.runs.worker.runner import LocalRunner
from tests.files.support import mark_mounted, ready_upload
from tests.runs.worker_support import (
    LEASE,
    attempts,
    claim,
    coordinator,
    create_user,
    expire_lease,
    local_runner,
    make_eligible_now,
    open_run_uses,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration


def maintenance(
    factory: sessionmaker[Session], storage: FileStorage, runner: LocalRunner
) -> Maintenance:
    config = MaintenanceConfig(stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER)
    return Maintenance(factory, storage, runner, config)


def set_run(factory: sessionmaker[Session], run_id: uuid.UUID, **values: object) -> None:
    with unit_of_work(factory) as session:
        session.execute(update(run).where(run.c.id == run_id).values(**values))


def test_lease_vencido_sem_executor_reagenda_sem_gastar_tentativa(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claim(factory, storage)
    expire_lease(factory, run_id)

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert counts["leases_reconciled"] == 1
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["generation"]) == ("running", "retry_wait", 1)
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is not None
    assert only["executor_started_at"] is None
    assert open_run_uses(factory, run_id) == 1


def test_lease_vivo_nao_e_reconciliado(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claim(factory, storage)

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert counts["leases_reconciled"] == 0
    assert run_row(factory, run_id)["phase"] == "inspecting"


def test_quinto_claim_termina_infra_poison(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    janitor = maintenance(factory, storage, local_runner(work_root))
    for _ in range(4):
        claim(factory, storage)
        expire_lease(factory, run_id)
        janitor.run_cycle()
        make_eligible_now(factory, run_id)

    assert claim_next(factory, storage, LEASE) is None

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"], row["claim_count"]) == ("failed", "INFRA_POISON", 4)
    assert len(attempts(factory, run_id)) == 4
    assert open_run_uses(factory, run_id) == 0


def test_fila_vencida_termina_queue_expired(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    set_run(factory, run_id, queue_expires_at=func.now() - timedelta(seconds=1))

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert counts["queue_expired"] == 1
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("failed", "QUEUE_EXPIRED")
    assert open_run_uses(factory, run_id) == 0


def test_prazo_vencido_em_retry_wait_termina_deadline_exceeded(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    janitor = maintenance(factory, storage, local_runner(work_root))
    claim(factory, storage)
    expire_lease(factory, run_id)
    janitor.run_cycle()
    set_run(factory, run_id, deadline_at=func.now() - timedelta(seconds=1))

    counts = janitor.run_cycle()

    assert counts["deadlines"] == 1
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("failed", "DEADLINE_EXCEEDED")
    assert open_run_uses(factory, run_id) == 0


def test_prazo_vencido_com_executor_possivelmente_vivo_vai_para_stopping(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    claim(factory, storage)
    set_run(factory, run_id, deadline_at=func.now() - timedelta(seconds=1))

    maintenance(factory, storage, local_runner(work_root)).run_cycle()

    row = run_row(factory, run_id)
    assert (row["status"], row["stop_reason"]) == ("stopping", "DEADLINE_EXCEEDED")
    assert attempts(factory, run_id)[0]["ended_at"] is None


def test_stopping_com_lease_vencido_para_o_executor_e_confirma(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    runner = local_runner(work_root, "sleep", "30")
    worker = coordinator(factory, storage, runner)
    claimed = claim(factory, storage)
    worker.prepare(claimed)
    runner.start(claimed.attempt_id, claimed.executor_image)
    worker.record_start(claimed)
    # O worker sumiu aqui: ninguém renova o lease nem atende ao cancelamento.
    cancel_run(factory, storage, owner, run_id)
    expire_lease(factory, run_id)

    maintenance(factory, storage, runner).run_cycle()

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("canceled", "CANCELED")
    assert not runner.status(claimed.attempt_id).is_running
    assert attempts(factory, run_id)[0]["error_code"] == "CANCELED"
    assert open_run_uses(factory, run_id) == 0


def test_candidatos_de_tentativa_encerrada_sao_descartados(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    claimed = claim(factory, storage)
    with unit_of_work(factory) as session:
        pending = create_candidate_object(
            session, owner, run_id, claimed.attempt_id, claimed.generation
        )
        committed = create_candidate_object(
            session, owner, run_id, claimed.attempt_id, claimed.generation
        )
    sha256, size = storage.create_exclusive(committed.storage_key, [b"x"], 1)
    storage.create_exclusive(pending.storage_key, [b"y"], 1)
    with unit_of_work(factory) as session:
        commit_candidate(session, owner, committed.object_id, sha256, size)
    expire_lease(factory, run_id)

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert counts["candidates_discarded"] == 1
    with factory() as session:
        states = dict(
            session.execute(
                select(stored_object.c.id, stored_object.c.state).where(
                    stored_object.c.attempt_id == claimed.attempt_id
                )
            ).all()
        )
    # O confirmado é pedido para exclusão e o expurgo do mesmo ciclo o remove; o pendente sai.
    assert states == {committed.object_id: ObjectState.DELETED}
    for key in (pending.storage_key, committed.storage_key):
        with pytest.raises(FileNotFoundError):
            (storage.root / key).stat()


def test_candidatos_da_tentativa_viva_sao_mantidos(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    claimed = claim(factory, storage)
    with unit_of_work(factory) as session:
        create_candidate_object(session, owner, run_id, claimed.attempt_id, claimed.generation)

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert counts["candidates_discarded"] == 0


def test_usos_de_execucao_terminal_sao_liberados(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    # O worker caiu entre o término e a transação que libera os usos.
    set_run(factory, run_id, status="failed", error_code="QUEUE_EXPIRED", finished_at=func.now())

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert counts["holds_released"] == 1
    assert open_run_uses(factory, run_id) == 0


def test_ciclo_inclui_a_retencao_de_arquivos(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert {
        "incomplete_uploads_expired",
        "unused_uploads_expired",
        "artifacts_expired",
        "objects_purged",
    } <= counts.keys()


# --- Objetos ausentes do storage (1.7, 3.8) ---------------------------------------------------


def lose_file(factory: sessionmaker[Session], storage: FileStorage) -> uuid.UUID:
    """Upload pronto cujo arquivo sumiu do disco, como depois de restaurar um backup antigo."""
    view = ready_upload(factory, storage, create_user(factory, f"conta-{uuid.uuid4()}"), b"abc")
    with factory() as session:
        row = session.execute(
            select(stored_object.c.id, stored_object.c.storage_key)
            .join(upload, upload.c.object_id == stored_object.c.id)
            .where(upload.c.id == view.id)
        ).one()
    storage.delete(row.storage_key)
    object_id: uuid.UUID = row.id
    return object_id


def alert_fields(caplog: pytest.LogCaptureFixture, alert: str) -> list[dict[str, object]]:
    fields = [getattr(record, "fields", {}) for record in caplog.records]
    return [f for f in fields if f.get("alert") == alert]


def object_state(factory: sessionmaker[Session], object_id: uuid.UUID) -> str:
    with factory() as session:
        state: str = session.execute(
            select(stored_object.c.state).where(stored_object.c.id == object_id)
        ).scalar_one()
    return state


def test_ciclo_marca_missing_o_objeto_sem_arquivo_e_alerta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lost = lose_file(factory, storage)
    ready_upload(factory, storage, create_user(factory, "outra"), b"zz")
    mark_mounted(storage)
    cycles = maintenance(factory, storage, local_runner(work_root))

    with caplog.at_level(logging.WARNING, logger="goblinshift.runs.maintenance"):
        first = cycles.run_cycle()
    second = cycles.run_cycle()

    assert (first["objects_missing_checked"], first["objects_marked_missing"]) == (2, 1)
    assert first["missing_sweep_skipped"] == 0
    assert object_state(factory, lost) == ObjectState.MISSING
    assert second["objects_marked_missing"] == 0
    assert alert_fields(caplog, "objects_missing") == [{"alert": "objects_missing", "objects": 1}]


def test_varredura_de_ausentes_continua_de_onde_parou_no_ciclo_seguinte(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    lost = [lose_file(factory, storage) for _ in range(2)]
    mark_mounted(storage)
    config = MaintenanceConfig(
        stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER, missing_sweep_batch_size=1
    )
    cycles = Maintenance(factory, storage, local_runner(work_root), config)

    marked = [cycles.run_cycle()["objects_marked_missing"] for _ in range(2)]

    assert marked == [1, 1]
    assert {object_state(factory, object_id) for object_id in lost} == {ObjectState.MISSING}


def test_storage_sem_sentinela_pula_a_varredura_e_alerta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = create_user(factory)
    view = ready_upload(factory, storage, owner, b"abc")
    cycles = maintenance(factory, storage, local_runner(work_root))

    with caplog.at_level(logging.WARNING, logger="goblinshift.runs.maintenance"):
        counts = cycles.run_cycle()

    assert (counts["objects_marked_missing"], counts["missing_sweep_skipped"]) == (0, 1)
    with factory() as session:
        assert get_upload(session, owner, view.id).availability is Availability.AVAILABLE
    assert alert_fields(caplog, "storage_unreachable") == [{"alert": "storage_unreachable"}]


def test_lote_com_ausentes_demais_aciona_o_limitador_e_alerta(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lost = [lose_file(factory, storage) for _ in range(GOVERNOR_MIN_SAMPLE)]
    mark_mounted(storage)
    cycles = maintenance(factory, storage, local_runner(work_root))

    with caplog.at_level(logging.WARNING, logger="goblinshift.runs.maintenance"):
        counts = cycles.run_cycle()

    assert (counts["objects_missing_absent"], counts["objects_marked_missing"]) == (10, 0)
    assert counts["missing_governor_tripped"] == 1
    assert {object_state(factory, object_id) for object_id in lost} == {ObjectState.COMMITTED}
    assert alert_fields(caplog, "missing_governor") == [
        {"alert": "missing_governor", "checked": 10, "absent": 10}
    ]
