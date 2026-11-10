"""Lock da área com prazo: quem chega atrás de uma operação travada desiste, não empilha."""

import os
import time
import uuid
from pathlib import Path

import pytest

from executor_ctl.docker_cli import DockerCli
from executor_ctl.service import AREA_LOCK_WAIT_S, AreaBusyError, ExecutorService

from .conftest import make_config
from .stuck_docker import StuckDocker, holding_area

pytestmark = pytest.mark.usefixtures("synthetic_policy_metadata")

HOLDER = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abcd")
LATECOMER = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abce")
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
# Curto para o teste; o de produção é AREA_LOCK_WAIT_S.
TEST_LOCK_WAIT_S = 0.2
# Folga para o agendador da máquina de CI; ainda bem abaixo do prazo de produção.
SCHEDULING_SLACK_S = 2.0


def service_with(tmp_path: Path, docker: DockerCli, *, lock_wait_s: float) -> ExecutorService:
    config = make_config(tmp_path, tmp_path, allowed=[IMAGE])
    return ExecutorService(config, docker, os.getgid(), area_lock_wait_s=lock_wait_s)


def test_prazo_de_producao_do_lock_e_curto_e_positivo() -> None:
    # Literal de propósito: abaixo do menor prazo do cliente que espera o lock (10 s no remove).
    assert AREA_LOCK_WAIT_S == 5.0


@pytest.mark.parametrize("latecomer", ["start", "remove"])
def test_operacao_atras_de_um_remove_travado_desiste_no_prazo(
    tmp_path: Path, latecomer: str
) -> None:
    docker = StuckDocker()
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)

    with holding_area(docker, lambda: service.remove(HOLDER)):
        started = time.monotonic()
        with pytest.raises(AreaBusyError):
            if latecomer == "start":
                service.start(LATECOMER, IMAGE)
            else:
                service.remove(LATECOMER)
        elapsed = time.monotonic() - started

    assert TEST_LOCK_WAIT_S <= elapsed < TEST_LOCK_WAIT_S + SCHEDULING_SLACK_S
    # Quem desistiu não chegou ao Docker nem à área.
    assert docker.calls == [f"inspect gs-exec-{HOLDER}", f"rm gs-exec-{HOLDER}"]
    assert not (tmp_path / "exec-work" / str(LATECOMER)).exists()


def test_operacao_atras_de_um_start_travado_desiste_no_prazo(tmp_path: Path) -> None:
    docker = StuckDocker()
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)
    (tmp_path / "exec-work" / str(HOLDER) / "inputs").mkdir(parents=True)

    with (
        holding_area(docker, lambda: service.start(HOLDER, IMAGE)),
        pytest.raises(AreaBusyError),
    ):
        service.remove(LATECOMER)

    assert f"rm gs-exec-{LATECOMER}" not in docker.calls


def test_lock_volta_a_servir_depois_que_a_operacao_a_frente_termina(tmp_path: Path) -> None:
    docker = StuckDocker()
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)

    with holding_area(docker, lambda: service.remove(HOLDER)), pytest.raises(AreaBusyError):
        service.remove(LATECOMER)

    # A desistência não deixou o lock preso: a mesma operação agora passa.
    service.remove(LATECOMER)

    assert docker.calls[-1] == f"rm gs-exec-{LATECOMER}"


def test_status_nao_espera_o_lock_da_area(tmp_path: Path) -> None:
    docker = StuckDocker()
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)

    with holding_area(docker, lambda: service.remove(HOLDER)):
        status = service.status(LATECOMER)

    assert status.state == "absent"
