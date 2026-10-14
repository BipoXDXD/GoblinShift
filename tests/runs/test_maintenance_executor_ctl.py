"""Manutenção com o Runner de produção, contra o `executor-ctl` falso (3.4, fatia 4).

Lease vencido não prova que o executor morreu: a manutenção consulta o ctl pelo nome, para o
que estiver vivo e só reagenda com o fim confirmado; o que não consegue inspecionar, não toca.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from tests.runs.fake_executor_ctl import IMAGE, FakeContainer, FakeExecutorCtl
from tests.runs.worker_support import (
    attempts,
    claim,
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
    run_id = queue_run(factory, storage, create_user(factory), executor_image=IMAGE)
    claimed = claim(factory, storage)
    runner.create_work_dir(claimed.attempt_id)
    expire_lease(factory, run_id)
    return run_id, claimed.attempt_id


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
