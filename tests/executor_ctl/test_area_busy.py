"""Área ocupada no protocolo: 503 `busy` no servidor real e falha transitória no worker."""

import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from executor_ctl.client import call
from goblinshift.runs.worker.executor_ctl_runner import ExecutorCtlError, ExecutorCtlRunner
from goblinshift.runs.worker.runner import RunnerError

from .conftest import make_config, running_server
from .stuck_docker import StuckDocker, holding_area

pytestmark = pytest.mark.usefixtures("synthetic_policy_metadata")

HOLDER = "0193a3f0-7b2c-7def-8a01-23456789abcd"
LATECOMER = "0193a3f0-7b2c-7def-8a01-23456789abce"
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
TEST_LOCK_WAIT_S = 0.2
# Folga para o agendador da máquina de CI.
SCHEDULING_SLACK_S = 2.0


@pytest.fixture
def docker() -> StuckDocker:
    return StuckDocker()


@pytest.fixture
def ctl_socket(tmp_path: Path, socket_dir: Path, docker: StuckDocker) -> Iterator[Path]:
    config = make_config(tmp_path, socket_dir, allowed=[IMAGE])
    with running_server(config, docker, area_lock_wait_s=TEST_LOCK_WAIT_S) as socket:
        yield socket


def remove_holder(ctl_socket: Path, statuses: list[int]) -> None:
    statuses.append(call(ctl_socket, "DELETE", f"/v1/attempts/{HOLDER}").status)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", f"/v1/attempts/{LATECOMER}/start", {"image": IMAGE}),
        ("DELETE", f"/v1/attempts/{LATECOMER}", None),
    ],
)
def test_area_ocupada_responde_503_busy_dentro_do_prazo(
    ctl_socket: Path,
    docker: StuckDocker,
    method: str,
    path: str,
    body: dict[str, object] | None,
) -> None:
    holder_statuses: list[int] = []

    with holding_area(docker, lambda: remove_holder(ctl_socket, holder_statuses)):
        started = time.monotonic()
        response = call(ctl_socket, method, path, body)
        elapsed = time.monotonic() - started

    assert response.status == 503
    assert response.body == {"error": "busy", "detail": response.body["detail"]}
    assert TEST_LOCK_WAIT_S <= elapsed < TEST_LOCK_WAIT_S + SCHEDULING_SLACK_S
    # A operação à frente terminou normalmente depois de solta.
    assert holder_statuses == [204]


def test_worker_recebe_busy_como_falha_transitoria_do_runner(
    ctl_socket: Path, docker: StuckDocker, tmp_path: Path
) -> None:
    runner = ExecutorCtlRunner(ctl_socket, tmp_path / "exec-work", work_group=os.getgid())
    holder_statuses: list[int] = []

    with (
        holding_area(docker, lambda: remove_holder(ctl_socket, holder_statuses)),
        pytest.raises(ExecutorCtlError) as error,
    ):
        runner.remove(uuid.UUID(LATECOMER))

    # RunnerError é o que coordenador e manutenção tratam como transitório (tentar de novo).
    assert isinstance(error.value, RunnerError)
    assert error.value.code == "busy"


def test_status_responde_enquanto_a_area_esta_ocupada(
    ctl_socket: Path, docker: StuckDocker
) -> None:
    holder_statuses: list[int] = []

    with holding_area(docker, lambda: remove_holder(ctl_socket, holder_statuses)):
        response = call(ctl_socket, "GET", f"/v1/attempts/{LATECOMER}")

    assert response.status == 200
    assert response.body["state"] == "absent"
