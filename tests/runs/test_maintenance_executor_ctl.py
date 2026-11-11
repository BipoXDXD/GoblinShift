"""Manutenção com o Runner de produção, contra o `executor-ctl` falso (3.4, fatia 4).

Lease vencido não prova que o executor morreu: a manutenção consulta o ctl pelo nome, para o
que estiver vivo e só reagenda com o fim confirmado; o que não consegue inspecionar, não toca.
"""

import logging
import os
import signal
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, text, update
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.shutdown import ShutdownFlag
from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory, unit_of_work
from goblinshift.platform.settings import Settings
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER, Status
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from goblinshift.runs.tables import attempt
from goblinshift.runs.worker.claim import ClaimedAttempt
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from tests.runs.fake_executor_ctl import IMAGE, FakeContainer, FakeExecutorCtl
from tests.runs.test_maintenance_steps import connection_refused
from tests.runs.worker_support import (
    attempts,
    claim,
    coordinator,
    create_user,
    expire_lease,
    open_run_uses,
    queue_run,
    run_row,
    snapshot,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def area(tmp_path: Path) -> Path:
    area = tmp_path / "exec-work"
    area.mkdir()
    return area


@pytest.fixture
def ctl(area: Path) -> Iterator[FakeExecutorCtl]:
    fake = FakeExecutorCtl(area)
    yield fake
    fake.close()


@pytest.fixture
def runner(ctl: FakeExecutorCtl, area: Path) -> ExecutorCtlRunner:
    return ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid())


def janitor(
    factory: sessionmaker[Session], storage: FileStorage, runner: ExecutorCtlRunner
) -> Maintenance:
    config = MaintenanceConfig(stop_grace=timedelta(seconds=1), jitter=lambda: MIN_RETRY_JITTER)
    return Maintenance(factory, storage, runner, config)


def abandoned_attempt(
    factory: sessionmaker[Session], storage: FileStorage, runner: ExecutorCtlRunner
) -> tuple[uuid.UUID, uuid.UUID]:
    """Run com tentativa reivindicada, área criada e lease vencido (o worker sumiu)."""
    claimed = paused_worker_attempt(factory, storage, runner)
    return claimed.run_id, claimed.attempt_id


def paused_worker_attempt(
    factory: sessionmaker[Session],
    storage: FileStorage,
    runner: ExecutorCtlRunner,
    subject: str = "ana",
) -> ClaimedAttempt:
    """Como `abandoned_attempt`, devolvendo o claim: o worker pode voltar e renovar o lease."""
    run_id = queue_run(factory, storage, create_user(factory, subject), executor_image=IMAGE)
    claimed = claim(factory, storage)
    runner.create_work_dir(claimed.attempt_id)
    expire_lease(factory, run_id)
    return claimed


def test_executor_vivo_e_parado_antes_de_reagendar(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    run_id, attempt_id = abandoned_attempt(factory, storage, runner)
    ctl.containers[str(attempt_id)] = FakeContainer("running")
    attempt_open_at_stop: list[bool] = []
    ctl.on_stop = lambda _id: attempt_open_at_stop.append(
        attempts(factory, run_id)[0]["ended_at"] is None
    )

    counts = janitor(factory, storage, runner).run_cycle()

    assert counts["leases_reconciled"] == 1
    assert attempt_open_at_stop == [True]
    assert ctl.operations(attempt_id) == ["status", "stop", "remove"]
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    assert attempts(factory, run_id)[0]["ended_at"] is not None
    assert not runner.work_dir(attempt_id).exists()


def test_lease_renovado_durante_a_consulta_nao_para_o_executor(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    claimed = paused_worker_attempt(factory, storage, runner)
    ctl.containers[str(claimed.attempt_id)] = FakeContainer("running")
    worker = coordinator(factory, storage, runner)
    renewed: list[Status] = []

    def worker_wakes_up(_attempt_id: str) -> None:
        # O worker pausado volta entre a leitura do lease vencido e a decisão de parar, com a
        # renovação de verdade (`lock_fenced` + UPDATE), enquanto a manutenção espera o ctl.
        if not renewed:
            renewed.append(worker.renew_lease(claimed))

    ctl.on_status = worker_wakes_up

    counts = janitor(factory, storage, runner).run_cycle()

    assert renewed == [Status.RUNNING]
    assert counts["leases_reconciled"] == 0
    assert ctl.operations(claimed.attempt_id) == ["status"]
    assert ctl.containers[str(claimed.attempt_id)].state == "running"
    [only] = attempts(factory, claimed.run_id)
    assert only["ended_at"] is None
    row = run_row(factory, claimed.run_id)
    assert (row["status"], row["generation"]) == ("running", claimed.generation)
    assert runner.work_dir(claimed.attempt_id).exists()


def test_tentativa_reconciliada_por_outra_manutencao_nao_e_parada_de_novo(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    claimed = paused_worker_attempt(factory, storage, runner)
    ctl.containers[str(claimed.attempt_id)] = FakeContainer("running")
    other = janitor(factory, storage, runner)
    other_counts: list[dict[str, int]] = []

    def other_janitor_decides(_attempt_id: str) -> None:
        # Outra réplica da manutenção para e decide a mesma tentativa depois de esta ler o
        # executor vivo; o gancho sai antes, para a outra não se chamar de novo.
        ctl.on_status = None
        other_counts.append(other.run_cycle())

    ctl.on_status = other_janitor_decides

    counts = janitor(factory, storage, runner).run_cycle()

    assert [other["leases_reconciled"] for other in other_counts] == [1]
    assert counts["leases_reconciled"] == 0
    assert ctl.operations(claimed.attempt_id) == ["status", "status", "stop", "remove"]
    row = run_row(factory, claimed.run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    assert attempts(factory, claimed.run_id)[0]["ended_at"] is not None


def expire_lease_ago(factory: sessionmaker[Session], attempt_id: uuid.UUID, ago: timedelta) -> None:
    with unit_of_work(factory) as session:
        session.execute(
            update(attempt)
            .where(attempt.c.id == attempt_id)
            .values(lease_expires_at=func.now() - ago)
        )


def test_releitura_que_estoura_o_statement_timeout_nao_derruba_o_lote(
    db_settings: Settings,
    engine: Engine,
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = paused_worker_attempt(factory, storage, runner, "ana")
    second = paused_worker_attempt(factory, storage, runner, "bia")
    expire_lease_ago(factory, first.attempt_id, timedelta(minutes=2))
    expire_lease_ago(factory, second.attempt_id, timedelta(minutes=1))
    for claimed in (first, second):
        ctl.containers[str(claimed.attempt_id)] = FakeContainer("running")
    # A releitura da primeira espera uma trava exclusiva na tabela e estoura o
    # `statement_timeout` (57014) de verdade; a trava sai no `status` da segunda.
    blocker = engine.connect()
    held = blocker.begin()

    def lock_attempts_during_first(attempt_id: str) -> None:
        if attempt_id == str(first.attempt_id):
            blocker.execute(text("LOCK TABLE attempt IN ACCESS EXCLUSIVE MODE"))
        else:
            held.rollback()

    ctl.on_status = lock_attempts_during_first
    short = db_settings.model_copy(
        update={"db_statement_timeout_ms": 300, "db_lock_timeout_ms": 5_000}
    )
    impatient_engine = make_engine(short)
    try:
        janitor_ = janitor(make_session_factory(impatient_engine), storage, runner)
        with caplog.at_level(logging.WARNING, logger="goblinshift.runs.maintenance"):
            reconciled = janitor_.reconcile_expired_leases()
    finally:
        blocker.close()
        impatient_engine.dispose()

    assert reconciled == 1
    assert ctl.operations(first.attempt_id) == ["status"]
    assert ctl.containers[str(first.attempt_id)].state == "running"
    assert attempts(factory, first.run_id)[0]["ended_at"] is None
    assert ctl.operations(second.attempt_id) == ["status", "stop", "remove"]
    assert attempts(factory, second.run_id)[0]["ended_at"] is not None
    [failure] = [
        record.fields  # type: ignore[attr-defined]
        for record in caplog.records
        if record.getMessage().startswith("releitura do lease falhou")
    ]
    assert failure == {
        "attempt_id": str(first.attempt_id),
        "run_id": str(first.run_id),
        "error_type": "OperationalError",
        "sqlstate": "57014",
    }


def test_releitura_sem_conexao_sobe_e_pula_os_passos_de_banco(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claimed = paused_worker_attempt(factory, storage, runner)
    ctl.containers[str(claimed.attempt_id)] = FakeContainer("running")
    janitor_ = janitor(factory, storage, runner)

    def unreachable(_expired: object) -> bool:
        raise connection_refused()

    monkeypatch.setattr(janitor_, "_lease_still_expired", unreachable)

    counts = janitor_.run_cycle()

    assert ctl.operations(claimed.attempt_id) == ["status"]
    assert counts["steps_failed"] == 1
    assert counts["steps_skipped"] > 0
    assert attempts(factory, claimed.run_id)[0]["ended_at"] is None


def test_executor_ausente_reagenda_sem_gastar_tentativa(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    run_id, attempt_id = abandoned_attempt(factory, storage, runner)

    counts = janitor(factory, storage, runner).run_cycle()

    assert counts["leases_reconciled"] == 1
    assert ctl.operations(attempt_id) == ["status", "remove"]
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["generation"]) == ("running", "retry_wait", 1)
    [only] = attempts(factory, run_id)
    assert (only["ended_at"] is not None, only["executor_started_at"]) == (True, None)
    assert open_run_uses(factory, run_id) == 1


def test_ctl_fora_do_ar_nao_toca_a_execucao(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    _run_id, attempt_id = abandoned_attempt(factory, storage, runner)
    ctl.close()
    before = snapshot(factory)

    counts = janitor(factory, storage, runner).run_cycle()

    assert counts["leases_reconciled"] == 0
    assert snapshot(factory) == before
    assert runner.work_dir(attempt_id).exists()


def test_parada_nao_confirmada_nao_toca_a_execucao(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    _run_id, attempt_id = abandoned_attempt(factory, storage, runner)
    ctl.containers[str(attempt_id)] = FakeContainer("running")
    ctl.unstoppable = True
    before = snapshot(factory)

    counts = janitor(factory, storage, runner).run_cycle()

    assert counts["leases_reconciled"] == 0
    assert snapshot(factory) == before
    assert "remove" not in ctl.operations(attempt_id)


def test_remove_que_falha_depois_da_decisao_fica_para_a_limpeza(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    run_id, attempt_id = abandoned_attempt(factory, storage, runner)
    original = ctl.handle

    def refuse_remove(method: str, path: str, body: bytes) -> tuple[int, dict[str, object] | None]:
        if method == "DELETE":
            return 503, {"error": "docker_unavailable", "detail": "falso"}
        return original(method, path, body)

    ctl.handle = refuse_remove  # type: ignore[method-assign]

    counts = janitor(factory, storage, runner).run_cycle()

    assert counts["leases_reconciled"] == 1
    assert run_row(factory, run_id)["phase"] == "retry_wait"
    assert runner.work_dir(attempt_id).exists()

    ctl.handle = original  # type: ignore[method-assign]
    counts = janitor(factory, storage, runner).run_cycle()

    assert counts["work_areas_removed"] == 1
    assert not runner.work_dir(attempt_id).exists()


def test_limpeza_remove_areas_de_tentativas_encerradas_e_mantem_as_vivas(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
    area: Path,
) -> None:
    queue_run(factory, storage, create_user(factory), executor_image=IMAGE)
    live = claim(factory, storage)
    runner.create_work_dir(live.attempt_id)
    # Área sem tentativa no banco (o worker só cria a área depois do commit do claim, então ela
    # nunca é de uma tentativa viva) e a pasta reservada do ext4.
    orphan = uuid.uuid7()
    runner.create_work_dir(orphan)
    (area / "lost+found").mkdir()

    counts = janitor(factory, storage, runner).run_cycle()

    assert counts["work_areas_removed"] == 1
    assert runner.work_dir(live.attempt_id).exists()
    assert not runner.work_dir(orphan).exists()
    assert (area / "lost+found").exists()
    assert "remove" not in ctl.operations(live.attempt_id)


def test_limpeza_com_ctl_fora_do_ar_mantem_a_area(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    orphan = uuid.uuid7()
    runner.create_work_dir(orphan)
    ctl.close()

    counts = janitor(factory, storage, runner).run_cycle()

    assert counts["work_areas_removed"] == 0
    assert runner.work_dir(orphan).exists()


# Parada pedida no meio do lote de um passo (lacuna L3) -------------------------------------


def test_parada_pedida_no_lote_termina_a_tentativa_em_curso_e_nao_toca_as_seguintes(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    batch = [
        paused_worker_attempt(factory, storage, runner, subject)
        for subject in ("ana", "bia", "cris")
    ]
    # O lote sai pela ordem do lease: a primeira vence há mais tempo.
    for minutes, claimed in zip((3, 2, 1), batch, strict=True):
        expire_lease_ago(factory, claimed.attempt_id, timedelta(minutes=minutes))
        ctl.containers[str(claimed.attempt_id)] = FakeContainer("running")
    first, *rest = batch
    shutdown = ShutdownFlag()
    # O SIGTERM do deploy chega enquanto o ctl para o executor da primeira tentativa.
    ctl.on_stop = lambda _id: shutdown.request(signal.SIGTERM)

    counts = janitor(factory, storage, runner).run_cycle(should_stop=shutdown.is_set)

    # A tentativa em curso termina inteira: parada, decidida e removida.
    assert counts["leases_reconciled"] == 1
    assert ctl.operations(first.attempt_id) == ["status", "stop", "remove"]
    assert attempts(factory, first.run_id)[0]["ended_at"] is not None
    assert not runner.work_dir(first.attempt_id).exists()
    # As seguintes ficam como estavam, para o próximo processo.
    for claimed in rest:
        assert ctl.operations(claimed.attempt_id) == []
        assert ctl.containers[str(claimed.attempt_id)].state == "running"
        assert attempts(factory, claimed.run_id)[0]["ended_at"] is None
        assert runner.work_dir(claimed.attempt_id).exists()


def test_parada_pedida_na_limpeza_remove_a_area_em_curso_e_mantem_as_seguintes(
    factory: sessionmaker[Session],
    storage: FileStorage,
    ctl: FakeExecutorCtl,
    runner: ExecutorCtlRunner,
) -> None:
    orphans = [uuid.uuid7() for _ in range(3)]
    for orphan in orphans:
        runner.create_work_dir(orphan)
    shutdown = ShutdownFlag()
    # O SIGTERM chega durante a consulta da primeira área; nenhuma tem executor.
    ctl.on_status = lambda _id: shutdown.request(signal.SIGTERM)

    counts = janitor(factory, storage, runner).run_cycle(should_stop=shutdown.is_set)

    assert counts["work_areas_removed"] == 1
    touched = [orphan for orphan in orphans if ctl.operations(orphan)]
    assert [ctl.operations(orphan) for orphan in touched] == [["status", "remove"]]
    assert [runner.work_dir(orphan).exists() for orphan in orphans].count(True) == 2
