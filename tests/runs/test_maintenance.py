"""Reconciliação da manutenção com PostgreSQL real (docs/GoblinShiftPlan.md, 3.4 e 3.5)."""

import errno
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.artifacts import commit_candidate, create_candidate_object
from goblinshift.files.limits import TRANSFER_MAX_DURATION, TRANSFER_SLOT_LEASE
from goblinshift.files.missing import GOVERNOR_MIN_SAMPLE
from goblinshift.files.objects import hold
from goblinshift.files.states import Availability, HolderKind, ObjectState
from goblinshift.files.storage import FileStorage, ObjectStat
from goblinshift.files.tables import object_use, stored_object, upload
from goblinshift.files.uploads import get_upload, request_deletion
from goblinshift.platform.db import unit_of_work
from goblinshift.runs.cancellation import cancel_run
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER
from goblinshift.runs.maintenance import UNREADABLE_LOG_SAMPLE, Maintenance, MaintenanceConfig
from goblinshift.runs.tables import run
from goblinshift.runs.worker.claim import claim_next
from goblinshift.runs.worker.runner import LocalRunner
from tests.capacity.support import account_reserved
from tests.files.support import create_intent, mark_mounted, ready_upload
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
    wait_until_executor_sleeps,
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
    # Sem esperar, o `stop` da manutenção caía sempre na partida do supervisor e do executor, e o
    # caminho seguido (grupo morto antes do executor nascer, ou executor nascido depois do sinal e
    # só parado pelo SIGKILL) dependia do escalonamento.
    wait_until_executor_sleeps(work_root, claimed.attempt_id)
    worker.record_start(claimed)
    # O worker sumiu aqui: ninguém renova o lease nem atende ao cancelamento.
    cancel_run(factory, storage, owner, run_id)
    expire_lease(factory, run_id)

    counts = maintenance(factory, storage, runner).run_cycle()

    row = run_row(factory, run_id)
    attempt_rows = attempts(factory, run_id)
    # Falhou uma vez sob carga sem reproduzir (#391): a mensagem diz qual passo ou estado caiu.
    diagnostic = (
        f"counts={counts} run=({row['status']}, {row['error_code']}) "
        f"attempts={[(a['error_code'], a['ended_at']) for a in attempt_rows]}"
    )
    assert counts["steps_failed"] == 0, diagnostic
    assert counts.get("leases_reconciled") == 1, diagnostic
    assert (row["status"], row["error_code"]) == ("canceled", "CANCELED"), diagnostic
    assert not runner.status(claimed.attempt_id).is_running, diagnostic
    assert attempt_rows[0]["error_code"] == "CANCELED", diagnostic
    assert open_run_uses(factory, run_id) == 0, diagnostic


def test_falha_ao_apagar_um_candidato_nao_perde_os_demais(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    claimed = claim(factory, storage)
    with unit_of_work(factory) as session:
        first = create_candidate_object(
            session, owner, run_id, claimed.attempt_id, claimed.generation
        )
        second = create_candidate_object(
            session, owner, run_id, claimed.attempt_id, claimed.generation
        )
    storage.create_exclusive(first.storage_key, [b"x"], 1)
    storage.create_exclusive(second.storage_key, [b"y"], 1)
    expire_lease(factory, run_id)
    delete = storage.delete
    candidates = {first.storage_key, second.storage_key}
    attempted: list[str] = []

    def refuse_first_candidate(key: str) -> None:
        if key in candidates:
            attempted.append(key)
            if len(attempted) == 1:
                raise PermissionError("disco recusou")
        delete(key)

    monkeypatch.setattr(storage, "delete", refuse_first_candidate)

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert counts["candidates_discarded"] == 1
    assert set(attempted) == candidates
    assert counts["candidates_left_on_disk"] == 1


def test_candidato_preso_ao_encerrar_a_run_conta_no_ciclo_e_nao_no_seguinte(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner)
    claimed = claim(factory, storage)
    with unit_of_work(factory) as session:
        candidate = create_candidate_object(
            session, owner, run_id, claimed.attempt_id, claimed.generation
        )
    storage.create_exclusive(candidate.storage_key, [b"x"], 1)
    cancel_run(factory, storage, owner, run_id)
    expire_lease(factory, run_id)
    delete = storage.delete

    def refuse_candidate(key: str) -> None:
        if key == candidate.storage_key:
            raise PermissionError("disco recusou")
        delete(key)

    monkeypatch.setattr(storage, "delete", refuse_candidate)
    worker = maintenance(factory, storage, local_runner(work_root))

    first = worker.run_cycle()
    second = worker.run_cycle()

    assert run_row(factory, run_id)["status"] == "canceled"
    assert first["candidates_left_on_disk"] == 1
    assert second["candidates_left_on_disk"] == 0


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
    assert counts["download_holds_released"] == 0
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


def test_arquivo_que_fica_no_disco_entra_na_contagem_do_ciclo(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = create_user(factory)
    ready = ready_upload(factory, storage, owner, b"abc")
    intent = create_intent(factory, owner)
    with unit_of_work(factory) as session:
        request_deletion(session, owner, ready.id)
        request_deletion(session, owner, intent.id)

    def refuse_delete(_key: str) -> None:
        raise PermissionError("disco recusou")

    monkeypatch.setattr(storage, "delete", refuse_delete)
    cycles = maintenance(factory, storage, local_runner(work_root))

    first = cycles.run_cycle()
    second = cycles.run_cycle()

    # Um do expurgo, já `deleted`, e um pendente, sem linha: nenhuma volta os repete.
    assert first["purge_files_not_removed"] == 2
    assert second["purge_files_not_removed"] == 0


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
        {"alert": "missing_governor", "checked": 10, "absent": 10, "unreadable": 0}
    ]


class EioStorage(FileStorage):
    """Storage montado cujo disco devolve EIO em todo `stat`."""

    def stat(self, key: str) -> ObjectStat:
        raise OSError(errno.EIO, "erro de entrada e saída", key)


class BrokenSweepStorage(FileStorage):
    """Storage em que até conferir a sentinela falha: a varredura inteira levanta."""

    def is_mounted(self) -> bool:
        raise OSError(errno.EIO, "erro de entrada e saída")


def test_disco_com_eio_nao_trava_a_manutencao_e_o_cursor_anda(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    for _ in range(2):
        ready_upload(factory, storage, create_user(factory, f"conta-{uuid.uuid4()}"), b"abc")
    mark_mounted(storage)
    config = MaintenanceConfig(
        stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER, missing_sweep_batch_size=1
    )
    cycles = Maintenance(factory, EioStorage(storage.root), local_runner(work_root), config)

    first, second, third = (cycles.run_cycle() for _ in range(3))

    assert [c["objects_missing_unreadable"] for c in (first, second, third)] == [1, 1, 0]
    assert [c["objects_marked_missing"] for c in (first, second)] == [0, 0]
    assert {"capacity_pools_corrected", "runs_deleted", "storage_global_max_bytes"} <= (
        first.keys()
    )


def storage_keys(factory: sessionmaker[Session]) -> list[str]:
    with factory() as session:
        return list(
            session.execute(
                select(stored_object.c.storage_key).order_by(stored_object.c.id)
            ).scalars()
        )


def test_lote_so_com_ilegiveis_alerta_com_errno_e_chave(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ready_upload(factory, storage, create_user(factory, "conta"), b"abc")
    [key] = storage_keys(factory)
    mark_mounted(storage)
    cycles = Maintenance(factory, EioStorage(storage.root), local_runner(work_root))

    with caplog.at_level(logging.WARNING, logger="goblinshift.runs.maintenance"):
        counts = cycles.run_cycle()

    assert (counts["objects_missing_absent"], counts["objects_missing_unreadable"]) == (0, 1)
    assert alert_fields(caplog, "objects_unreadable") == [
        {
            "alert": "objects_unreadable",
            "unreadable": 1,
            "errnos": {"EIO": 1},
            "sample": [{"storage_key": key, "errno_name": "EIO"}],
        }
    ]


def test_symlink_no_lugar_do_objeto_alerta_input_tampered_sem_caminho_nem_alvo(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ready_upload(factory, storage, create_user(factory, "conta"), b"abc")
    [key] = storage_keys(factory)
    link = storage.root.joinpath(*key.split("/"))
    link.unlink()
    link.symlink_to(tmp_path / "CANARY-alvo-fora-da-raiz")
    mark_mounted(storage)
    cycles = maintenance(factory, storage, local_runner(work_root))

    with caplog.at_level(logging.WARNING, logger="goblinshift.runs.maintenance"):
        counts = cycles.run_cycle()

    # Adulterado não é ausente nem ilegível: nenhum dos dois alertas dispara por ele.
    assert (counts["objects_missing_absent"], counts["objects_missing_unreadable"]) == (0, 0)
    assert (counts["objects_missing_tampered"], counts["objects_marked_missing"]) == (1, 0)
    assert alert_fields(caplog, "input_tampered") == [
        {"alert": "input_tampered", "tampered": 1, "sample": [{"storage_key": key}]}
    ]
    assert alert_fields(caplog, "objects_unreadable") == []
    assert alert_fields(caplog, "objects_missing") == []
    logged = " ".join(f"{r.getMessage()} {getattr(r, 'fields', {})}" for r in caplog.records)
    assert str(storage.root) not in logged
    assert "CANARY" not in logged
    assert link.is_symlink()


def test_disco_inteiro_ilegivel_gera_uma_linha_de_log_com_amostra_limitada(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for number in range(UNREADABLE_LOG_SAMPLE + 1):
        ready_upload(factory, storage, create_user(factory, f"conta-{number}"), b"abc")
    mark_mounted(storage)
    cycles = Maintenance(factory, EioStorage(storage.root), local_runner(work_root))

    with caplog.at_level(logging.WARNING, logger="goblinshift.runs.maintenance"):
        cycles.run_cycle()

    [fields] = alert_fields(caplog, "objects_unreadable")
    assert fields["unreadable"] == UNREADABLE_LOG_SAMPLE + 1
    assert fields["errnos"] == {"EIO": UNREADABLE_LOG_SAMPLE + 1}
    assert len(cast("list[object]", fields["sample"])) == UNREADABLE_LOG_SAMPLE


def test_falha_da_varredura_nao_leva_as_outras_etapas(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ready_upload(factory, storage, create_user(factory), b"abc")
    cycles = maintenance(factory, BrokenSweepStorage(storage.root), local_runner(work_root))

    with caplog.at_level(logging.ERROR, logger="goblinshift.runs.maintenance"):
        counts = cycles.run_cycle()

    assert counts["missing_sweep_failed"] == 1
    # Erro que não é de conexão fica isolado no passo: o ciclo não o conta como falho nem pula nada.
    assert counts["steps_failed"] == 0
    assert counts["steps_skipped"] == 0
    # Sentinela ilegível não é objeto ilegível: `causa-objetos-ilegiveis` não a vê.
    assert counts["objects_missing_unreadable"] == 0
    assert {"objects_purged", "capacity_pools_corrected", "runs_deleted"} <= counts.keys()
    assert "storage_global_max_bytes" in counts
    assert any(r.exc_info for r in caplog.records)


def plant_temporary(storage: FileStorage, name: str, age: timedelta, content: bytes) -> Path:
    directory = storage.root / ".tmp"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(content)
    modified_at = time.time() - age.total_seconds()
    os.utime(path, (modified_at, modified_at))
    return path


def test_temporario_abandonado_sai_no_ciclo_e_o_diretorio_e_medido(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    abandoned = plant_temporary(
        storage, f"{'a' * 32}.part", timedelta(hours=1, minutes=1), b"abandonado"
    )
    recent = plant_temporary(storage, f"{'b' * 32}.part", timedelta(minutes=59), b"recente")

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert not abandoned.exists()
    assert recent.exists()
    assert counts["tmp_parts_removed"] == 1
    assert counts["tmp_parts"] == 1
    assert counts["tmp_parts_bytes"] == len(b"recente")
    assert counts["tmp_parts_sweep_failed"] == 0


def test_falha_ao_varrer_temporarios_nao_impede_o_resto_do_ciclo(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage.root.mkdir(parents=True)
    # `.tmp` como arquivo: `scandir` levanta `NotADirectoryError` de verdade.
    (storage.root / ".tmp").write_bytes(b"")

    with caplog.at_level(logging.ERROR, logger="goblinshift.runs.maintenance"):
        counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert counts["tmp_parts_sweep_failed"] == 1
    assert counts["tmp_parts_removed"] == 0
    assert "storage_global_max_bytes" in counts
    assert "limpeza de temporários falhou" in caplog.messages


def test_falha_ao_apagar_um_temporario_marca_o_ciclo_e_limpa_os_demais(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = timedelta(hours=2)
    for name in (f"{'a' * 32}.part", f"{'b' * 32}.part"):
        plant_temporary(storage, name, old, b"ab")
    original = Path.unlink
    denied = iter([PermissionError(errno.EACCES, "recusado")])

    def unlink(self: Path, missing_ok: bool = False) -> None:
        for failure in denied:
            raise failure
        original(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert counts["tmp_parts_removed"] == 1
    assert counts["tmp_parts"] == 1
    assert counts["tmp_parts_bytes"] == len(b"ab")
    assert counts["tmp_parts_sweep_failed"] == 1


def test_uso_de_download_orfao_apos_queda_da_api_nao_impede_o_expurgo(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    """Issue #442: a API morreu no meio do download e o `finally` com `finish_download` não rodou.

    Passada a duração máxima de uma transferência (com folga), o uso aberto não pode segurar o
    objeto excluído no disco nem a reserva de capacidade da conta.
    """
    owner = create_user(factory)
    view = ready_upload(factory, storage, owner, b"abcd")
    with factory() as session:
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == view.id)
        ).scalar_one()
    with unit_of_work(factory) as session:
        hold(session, [object_id], HolderKind.DOWNLOAD, uuid.uuid7())
    # Sem `release`: o processo recebeu SIGKILL. O relógio do banco avança além do teto.
    with unit_of_work(factory) as session:
        session.execute(
            update(object_use)
            .where(object_use.c.object_id == object_id)
            .values(started_at=func.now() - TRANSFER_MAX_DURATION - timedelta(minutes=5))
        )
        request_deletion(session, owner, view.id)

    janitor = maintenance(factory, storage, local_runner(work_root))
    janitor.run_cycle()
    janitor.run_cycle()

    with factory() as session:
        state = session.execute(
            select(stored_object.c.state).where(stored_object.c.id == object_id)
        ).scalar_one()
    assert state == ObjectState.DELETED
    assert account_reserved(factory, owner) == 0


def _download_use(
    factory: sessionmaker[Session], storage: FileStorage, *, age: timedelta
) -> uuid.UUID:
    """Um uso de download aberto há `age`, pelo relógio do banco; devolve o detentor."""
    view = ready_upload(factory, storage, create_user(factory), b"abcd")
    holder_id = uuid.uuid7()
    with unit_of_work(factory) as session:
        object_id = session.execute(
            select(upload.c.object_id).where(upload.c.id == view.id)
        ).scalar_one()
        hold(session, [object_id], HolderKind.DOWNLOAD, holder_id)
        session.execute(
            update(object_use)
            .where(object_use.c.holder_id == holder_id)
            .values(started_at=func.now() - age)
        )
    return holder_id


def _use_ended_at(factory: sessionmaker[Session], holder_id: uuid.UUID) -> datetime | None:
    with factory() as session:
        ended_at: datetime | None = session.execute(
            select(object_use.c.ended_at).where(object_use.c.holder_id == holder_id)
        ).scalar_one()
    return ended_at


@pytest.mark.parametrize(
    ("age", "released"),
    [
        (TRANSFER_SLOT_LEASE - timedelta(seconds=30), False),
        (TRANSFER_SLOT_LEASE + timedelta(seconds=1), True),
    ],
)
def test_uso_de_download_so_e_encerrado_depois_do_lease_da_transferencia(
    factory: sessionmaker[Session],
    storage: FileStorage,
    work_root: Path,
    age: timedelta,
    released: bool,
) -> None:
    holder_id = _download_use(factory, storage, age=age)

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert (_use_ended_at(factory, holder_id) is not None) is released
    assert counts["download_holds_released"] == int(released)
    assert counts["holds_released"] == 0


def test_uso_de_execucao_nao_e_encerrado_pelo_lease_de_download(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    with unit_of_work(factory) as session:
        session.execute(
            update(object_use)
            .where(object_use.c.holder_id == run_id)
            .values(started_at=func.now() - TRANSFER_SLOT_LEASE - timedelta(hours=1))
        )

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert open_run_uses(factory, run_id) > 0
    assert counts["holds_released"] == 0
    assert counts["download_holds_released"] == 0


def test_uso_de_download_ja_encerrado_nao_muda(
    factory: sessionmaker[Session], storage: FileStorage, work_root: Path
) -> None:
    holder_id = _download_use(factory, storage, age=TRANSFER_SLOT_LEASE + timedelta(hours=1))
    with unit_of_work(factory) as session:
        session.execute(
            update(object_use)
            .where(object_use.c.holder_id == holder_id)
            .values(ended_at=func.now() - timedelta(hours=1))
        )
    ended_before = _use_ended_at(factory, holder_id)

    counts = maintenance(factory, storage, local_runner(work_root)).run_cycle()

    assert _use_ended_at(factory, holder_id) == ended_before
    assert counts["download_holds_released"] == 0
    assert counts["holds_released"] == 0
