"""Coordenador com o Runner de produção, contra o `executor-ctl` falso (3.4 a 3.6, fatia 4).

O falso "executa" o executor real (ou o de teste) num subprocesso que escreve na área de
trabalho, como o container faria em `/work`.
"""

import os
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.runs.cancellation import cancel_run
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlRunner
from tests.runs.fake_executor_ctl import IMAGE, FakeExecutorCtl
from tests.runs.worker_support import (
    FAKE_EXECUTOR,
    attempts,
    claim,
    coordinator,
    create_user,
    open_run_uses,
    queue_run,
    run_row,
)

pytestmark = pytest.mark.integration

REAL_EXECUTOR = (sys.executable, "-m", "goblinshift.processing.executor")
WAIT_S = 20.0
OTHER_IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "e" * 64


@pytest.fixture
def area(tmp_path: Path) -> Path:
    area = tmp_path / "exec-work"
    area.mkdir()
    return area


@pytest.fixture
def start_ctl(area: Path) -> Iterator[Callable[..., FakeExecutorCtl]]:
    started: list[FakeExecutorCtl] = []

    def start(command: Sequence[str] = REAL_EXECUTOR, **controls: object) -> FakeExecutorCtl:
        ctl = FakeExecutorCtl(area, command=command)
        for name, value in controls.items():
            setattr(ctl, name, value)
        started.append(ctl)
        return ctl

    yield start
    for ctl in started:
        ctl.close()


def ctl_runner(ctl: FakeExecutorCtl, area: Path) -> ExecutorCtlRunner:
    return ExecutorCtlRunner(ctl.socket_path, area, work_group=os.getgid())


def wait_until(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT_S
    while not condition():
        assert time.monotonic() < deadline, "condição não chegou a tempo"
        time.sleep(0.05)


def test_fluxo_feliz_pelo_ctl_publica_e_remove_a_area(
    factory: sessionmaker[Session],
    storage: FileStorage,
    area: Path,
    start_ctl: Callable[..., FakeExecutorCtl],
) -> None:
    ctl = start_ctl()
    run_id = queue_run(factory, storage, create_user(factory), executor_image=IMAGE)
    claimed = claim(factory, storage)

    coordinator(factory, storage, ctl_runner(ctl, area)).run(claimed)

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("succeeded", None)
    assert ctl.last_start_body == {"image": IMAGE}
    operations = ctl.operations(claimed.attempt_id)
    assert operations[0] == "start"
    assert operations[-1] == "remove"
    assert "stop" not in operations
    assert list(area.iterdir()) == []
    assert open_run_uses(factory, run_id) == 0


def test_cancelamento_pede_stop_ao_ctl_antes_de_confirmar(
    factory: sessionmaker[Session],
    storage: FileStorage,
    area: Path,
    start_ctl: Callable[..., FakeExecutorCtl],
) -> None:
    owner = create_user(factory)
    run_id = queue_run(factory, storage, owner, executor_image=IMAGE)
    status_at_stop: list[str] = []
    ctl = start_ctl(
        (sys.executable, FAKE_EXECUTOR, "sleep", "30"),
        on_stop=lambda _id: status_at_stop.append(run_row(factory, run_id)["status"]),
    )
    claimed = claim(factory, storage)
    worker = coordinator(factory, storage, ctl_runner(ctl, area))
    thread = threading.Thread(target=worker.run, args=(claimed,))
    thread.start()
    wait_until(lambda: attempts(factory, run_id)[0]["executor_started_at"] is not None)

    result = cancel_run(factory, storage, owner, run_id)
    thread.join(timeout=WAIT_S)

    assert result.status is Status.STOPPING
    assert status_at_stop[:1] == ["stopping"]
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("canceled", "CANCELED")
    operations = ctl.operations(claimed.attempt_id)
    assert operations.index("stop") < operations.index("remove")
    assert list(area.iterdir()) == []


def test_imagem_fora_da_allowlist_e_falha_transitoria(
    factory: sessionmaker[Session],
    storage: FileStorage,
    area: Path,
    start_ctl: Callable[..., FakeExecutorCtl],
) -> None:
    ctl = start_ctl()
    run_id = queue_run(factory, storage, create_user(factory), executor_image=OTHER_IMAGE)
    claimed = claim(factory, storage)

    coordinator(factory, storage, ctl_runner(ctl, area)).run(claimed)

    row = run_row(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    [only] = attempts(factory, run_id)
    assert (only["error_code"], only["executor_started_at"]) == ("EXECUTOR_UNAVAILABLE", None)
    assert list(area.iterdir()) == []


def test_ctl_fora_do_ar_no_start_e_falha_transitoria(
    factory: sessionmaker[Session],
    storage: FileStorage,
    area: Path,
    start_ctl: Callable[..., FakeExecutorCtl],
) -> None:
    ctl = start_ctl()
    ctl.close()
    run_id = queue_run(factory, storage, create_user(factory), executor_image=IMAGE)

    applied = coordinator(factory, storage, ctl_runner(ctl, area)).run(claim(factory, storage))

    assert applied is not None
    row = run_row(factory, run_id)
    assert (row["status"], row["phase"]) == ("running", "retry_wait")
    assert attempts(factory, run_id)[0]["error_code"] == "EXECUTOR_UNAVAILABLE"


def test_executor_morto_por_memoria_falha_sem_retentativa(
    factory: sessionmaker[Session],
    storage: FileStorage,
    area: Path,
    start_ctl: Callable[..., FakeExecutorCtl],
) -> None:
    ctl = start_ctl((sys.executable, FAKE_EXECUTOR, "exit", "137"), oom_on_exit=True)
    run_id = queue_run(factory, storage, create_user(factory), executor_image=IMAGE)

    coordinator(factory, storage, ctl_runner(ctl, area)).run(claim(factory, storage))

    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("failed", "RESOURCE_LIMIT_EXCEEDED")
    assert attempts(factory, run_id)[0]["error_code"] == "RESOURCE_LIMIT_EXCEEDED"


def test_tentativa_acima_do_teto_e_parada_e_falha_por_limite_de_recurso(
    factory: sessionmaker[Session],
    storage: FileStorage,
    area: Path,
    start_ctl: Callable[..., FakeExecutorCtl],
) -> None:
    ctl = start_ctl((sys.executable, FAKE_EXECUTOR, "sleep", "30"))
    run_id = queue_run(factory, storage, create_user(factory), executor_image=IMAGE)
    claimed = claim(factory, storage)
    worker = coordinator(
        factory, storage, ctl_runner(ctl, area), attempt_timeout=timedelta(seconds=0.5)
    )

    worker.run(claimed)

    # 1.6: os 10 min da tentativa são limite de recurso; repetir daria o mesmo.
    row = run_row(factory, run_id)
    assert (row["status"], row["error_code"]) == ("failed", "RESOURCE_LIMIT_EXCEEDED")
    assert attempts(factory, run_id)[0]["error_code"] == "RESOURCE_LIMIT_EXCEEDED"
    assert "stop" in ctl.operations(claimed.attempt_id)
