"""Cópia da entrada para a área de trabalho sob demora (docs/GoblinShiftPlan.md, 3.4 e 3.5).

PostgreSQL e executor reais. A cópia de uma entrada grande (até 512 MiB) dura mais que o lease:
ela renova o lease entre blocos, como a coleta e a gravação dos candidatos, e é nela que um
cancelamento ou a perda do fencing é percebido.
"""

import errno
import io
import logging
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, cast

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.runs.cancellation import cancel_run
from goblinshift.runs.lifecycle import MIN_RETRY_JITTER
from goblinshift.runs.maintenance import Maintenance, MaintenanceConfig
from goblinshift.runs.worker import coordinator as coordinator_module
from goblinshift.runs.worker.claim import claim_next
from tests.runs.worker_support import (
    LEASE,
    SlowStorage,
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
    snapshot,
)

pytestmark = pytest.mark.integration

HEADER = b"nome,cidade\r\n"
# Com blocos de 4 bytes cada entrada tem dezenas de blocos: sobra para 16 pausas de LEASE/8.
FIRST = HEADER + b"Ana,Recife\r\n" * 10
SECOND = HEADER + b"Bia,Olinda\r\n" * 10
PAUSES = 16
PAUSE = LEASE / 8


def test_lease_segue_renovado_durante_a_copia_da_entrada(
    factory: sessionmaker[Session],
    tmp_path: Path,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "COPY_CHUNK_BYTES", 4)
    storage = SlowStorage(tmp_path / "storage", factory)
    run_id = queue_run(
        factory, storage, create_user(factory), [("a.csv", FIRST), ("b.csv", SECOND)]
    )
    claimed = claim(factory, storage)
    storage.slow_reads(claimed.attempt_id, pauses=PAUSES, pause=PAUSE)

    applied = coordinator(factory, storage, local_runner(work_root)).run(claimed)

    assert applied is not None
    assert run_row(factory, run_id)["status"] == "succeeded"
    assert len(storage.lease_margins) == PAUSES
    # As pausas somam dois leases: sem renovar entre blocos o lease venceria no meio da cópia.
    assert PAUSES * PAUSE > LEASE
    assert min(storage.lease_margins) > 0


def test_cancelamento_durante_a_copia_conclui_pela_parada_sem_iniciar_o_executor(
    factory: sessionmaker[Session],
    tmp_path: Path,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "COPY_CHUNK_BYTES", 4)
    storage = SlowStorage(tmp_path / "storage", factory)
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner, [("a.csv", FIRST), ("b.csv", SECOND)])
    claimed = claim(factory, storage)
    runner = local_runner(work_root)
    canceled: list[bool] = []

    def cancel_once() -> None:
        if not canceled:
            canceled.append(True)
            cancel_run(factory, storage, owner, run_id)

    storage.slow_reads(claimed.attempt_id, pauses=PAUSES, pause=PAUSE, on_pause=cancel_once)

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    # A cópia parou na renovação seguinte ao cancelamento, sem esperar o fim das entradas.
    assert len(storage.lease_margins) < PAUSES
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"], row["stop_reason"]) == (
        "canceled",
        "CANCELED",
        "CANCELED",
    )
    [only] = attempts(factory, run_id)
    assert only["ended_at"] is not None
    assert only["executor_started_at"] is None
    assert open_run_uses(factory, run_id) == 0
    assert not runner.work_dir(claimed.attempt_id).exists()


class _NoSpaceOnClose(io.BytesIO):
    """O buffer só chega ao disco no fechamento, e é nele que o disco cheio aparece."""

    def close(self) -> None:
        super().close()
        raise OSError(errno.ENOSPC, "sem espaço no dispositivo")


def test_cancelamento_durante_a_copia_prevalece_sobre_falha_no_fechamento(
    factory: sessionmaker[Session],
    tmp_path: Path,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(coordinator_module, "COPY_CHUNK_BYTES", 4)
    real_create = coordinator_module._create_for_executor

    def create(path: Path) -> BinaryIO:
        if path.name == "0000.bin":
            return cast(BinaryIO, _NoSpaceOnClose())
        return real_create(path)

    monkeypatch.setattr(coordinator_module, "_create_for_executor", create)
    storage = SlowStorage(tmp_path / "storage", factory)
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner, [("a.csv", FIRST), ("b.csv", SECOND)])
    claimed = claim(factory, storage)
    runner = local_runner(work_root)
    canceled: list[bool] = []

    def cancel_once() -> None:
        if not canceled:
            canceled.append(True)
            cancel_run(factory, storage, owner, run_id)

    storage.slow_reads(claimed.attempt_id, pauses=PAUSES, pause=PAUSE, on_pause=cancel_once)

    with caplog.at_level(logging.WARNING):
        applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is not None
    # O fechamento falho não troca o cancelamento por falha de preparação nem por alerta falso
    # de `executor_start_failed`: a parada é a exceção original e conclui a Run.
    assert [r for r in caplog.records if r.getMessage() == "área de trabalho falhou"] == []
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"], row["stop_reason"]) == (
        "canceled",
        "CANCELED",
        "CANCELED",
    )
    [only] = attempts(factory, run_id)
    assert only["executor_started_at"] is None
    assert not runner.work_dir(claimed.attempt_id).exists()


def test_lease_tomado_durante_a_copia_abandona_sem_escrever(
    factory: sessionmaker[Session],
    tmp_path: Path,
    work_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coordinator_module, "COPY_CHUNK_BYTES", 4)
    storage = SlowStorage(tmp_path / "storage", factory)
    run_id = queue_run(
        factory, storage, create_user(factory), [("a.csv", FIRST), ("b.csv", SECOND)]
    )
    claimed = claim(factory, storage)
    runner = local_runner(work_root)
    taken: list[object] = []

    def other_worker_takes_over() -> None:
        if taken:
            return
        expire_lease(factory, run_id)
        janitor = Maintenance(
            factory,
            storage,
            runner,
            MaintenanceConfig(stop_grace=timedelta(seconds=2), jitter=lambda: MIN_RETRY_JITTER),
        )
        janitor.reconcile_expired_leases()
        make_eligible_now(factory, run_id)
        taken.append(claim_next(factory, storage, LEASE))
        taken.append(snapshot(factory))

    storage.slow_reads(
        claimed.attempt_id, pauses=PAUSES, pause=PAUSE, on_pause=other_worker_takes_over
    )

    applied = coordinator(factory, storage, runner).run(claimed)

    assert applied is None
    new_claim, written_by_other = taken
    assert new_claim is not None
    # O worker A parou na renovação seguinte, sem esperar o fim das entradas.
    assert len(storage.lease_margins) < PAUSES
    # Depois da tomada, A não escreveu nada no banco: a tentativa nova segue intacta.
    assert snapshot(factory) == written_by_other
    row = run_row(factory, run_id)
    assert (row["status"], row["generation"]) == ("running", 2)
    assert [item["ended_at"] is None for item in attempts(factory, run_id)] == [False, True]
