"""Worker como processo real: NOTIFY, SIGKILL e SIGSTOP além do lease (3.4 e 3.7 do plano)."""

import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import Settings
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from goblinshift.runs.tables import attempt
from goblinshift.runs.worker.claim import RUNS_CHANNEL
from tests.runs.worker_support import (
    attempts,
    create_user,
    local_runner,
    queue_run,
    run_row,
    snapshot,
)

pytestmark = pytest.mark.integration

WORKER_SCRIPT = str(Path(__file__).with_name("worker_process.py"))
LEASE_S = 1.5
WAIT_S = 30.0


def wait_until(condition: Callable[[], bool], timeout_s: float = WAIT_S) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "condição não chegou a tempo"
        time.sleep(0.05)


@contextmanager
def worker_process(
    settings: Settings,
    storage: FileStorage,
    work_root: Path,
    log: Path,
    *mode: str,
    poll_interval_s: float = 0.2,
) -> Iterator[subprocess.Popen[bytes]]:
    env = {
        **os.environ,
        "GS_ENVIRONMENT": "test",
        "GS_DB_HOST": settings.db_host,
        "GS_DB_PORT": str(settings.db_port),
        "GS_DB_USER": settings.db_user,
        "GS_DB_PASSWORD": settings.db_password.get_secret_value(),
        "GS_DB_NAME": settings.db_name,
        "GS_STORAGE_ROOT": str(storage.root),
        "GS_WORK_ROOT": str(work_root),
        "GS_WORKER_LEASE_S": str(LEASE_S),
        "GS_WORKER_POLL_INTERVAL_S": str(poll_interval_s),
    }
    with log.open("wb") as output:
        process = subprocess.Popen(  # noqa: S603 - script do próprio teste
            [sys.executable, WORKER_SCRIPT, *mode], env=env, stdout=output, stderr=output
        )
    try:
        wait_until(lambda: "worker pronto" in log.read_text(encoding="utf-8"))
        yield process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGCONT)
            process.kill()
        process.wait(timeout=WAIT_S)


def janitor(factory: sessionmaker[Session], storage: FileStorage, work_root: Path) -> Maintenance:
    config = MaintenanceConfig(stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER)
    return Maintenance(factory, storage, local_runner(work_root), config)


def executor_started(factory: sessionmaker[Session], run_id: uuid.UUID) -> bool:
    rows = attempts(factory, run_id)
    return bool(rows) and rows[-1]["executor_started_at"] is not None


def lease_expired(factory: sessionmaker[Session]) -> bool:
    with factory() as session:
        expired: bool = session.execute(
            select(func.bool_and(attempt.c.lease_expires_at < func.now())).where(
                attempt.c.ended_at.is_(None)
            )
        ).scalar_one()
    return expired


def test_notify_acorda_o_worker_antes_do_polling(
    factory: sessionmaker[Session],
    db_settings: Settings,
    storage: FileStorage,
    work_root: Path,
    tmp_path: Path,
) -> None:
    owner = create_user(factory)
    with worker_process(
        db_settings, storage, work_root, tmp_path / "worker.log", "exit", "0", poll_interval_s=60
    ):
        run_id = queue_run(factory, storage, owner)
        time.sleep(0.5)
        claimed_before_notify = run_row(factory, run_id)["claim_count"]
        with unit_of_work(factory) as session:
            session.execute(text(f"NOTIFY {RUNS_CHANNEL}"))

        wait_until(lambda: run_row(factory, run_id)["claim_count"] == 1, timeout_s=10)

    assert claimed_before_notify == 0


def test_worker_morto_por_sigkill_e_assumido_pela_manutencao(
    factory: sessionmaker[Session],
    db_settings: Settings,
    storage: FileStorage,
    work_root: Path,
    tmp_path: Path,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    with worker_process(
        db_settings, storage, work_root, tmp_path / "worker.log", "sleep", "30"
    ) as worker:
        wait_until(lambda: executor_started(factory, run_id))
        worker.kill()
        worker.wait(timeout=WAIT_S)
    [orphan] = attempts(factory, run_id)
    runner = local_runner(work_root)
    survived = runner.status(orphan["id"]).is_running
    wait_until(lambda: lease_expired(factory))

    counts = janitor(factory, storage, work_root).run_cycle()

    assert survived, "o executor deveria sobreviver ao worker, como um container"
    assert counts["leases_reconciled"] == 1
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    [ended] = attempts(factory, run_id)
    assert ended["ended_at"] is not None
    assert ended["executor_started_at"] is not None
    assert not runner.status(orphan["id"]).is_running


def test_worker_pausado_alem_do_lease_nao_escreve_ao_voltar(
    factory: sessionmaker[Session],
    db_settings: Settings,
    storage: FileStorage,
    work_root: Path,
    tmp_path: Path,
) -> None:
    run_id = queue_run(factory, storage, create_user(factory))
    log = tmp_path / "worker.log"
    with worker_process(db_settings, storage, work_root, log, "sleep", "30") as worker:
        wait_until(lambda: executor_started(factory, run_id))
        time.sleep(0.2)
        worker.send_signal(signal.SIGSTOP)
        wait_until(lambda: lease_expired(factory))
        janitor(factory, storage, work_root).run_cycle()
        after_takeover = snapshot(factory)

        worker.send_signal(signal.SIGCONT)
        wait_until(lambda: "lost_fencing" in log.read_text(encoding="utf-8"))

        assert snapshot(factory) == after_takeover
        assert worker.poll() is None, "o worker segue vivo e volta ao laço"
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"], row["generation"]) == ("running", "retry_wait", 1)


def test_sigterm_encerra_o_worker_ocioso(
    factory: sessionmaker[Session],
    db_settings: Settings,
    storage: FileStorage,
    work_root: Path,
    tmp_path: Path,
) -> None:
    log = tmp_path / "worker.log"
    with worker_process(db_settings, storage, work_root, log, "exit", "0") as worker:
        worker.send_signal(signal.SIGTERM)

        assert worker.wait(timeout=WAIT_S) == 0
    assert "worker encerrado" in log.read_text(encoding="utf-8")
