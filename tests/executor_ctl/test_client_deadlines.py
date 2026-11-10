"""Contrato de prazo do `start` e do `remove`: o worker espera mais que o pior caso do executor-ctl.

O worker não importa `deploy/executor_ctl` (o pacote é do host, fora da imagem do worker), então
o cliente espelha o prazo do CLI do Docker e quantas chamadas cabem em cada operação que toma o
lock da área. Aqui as duas pontas se encontram: o espelho bate com o servidor, e o número de
chamadas é medido no serviço real, com um Docker de mentira que só conta (nada espera de verdade).

Se o cliente desistir antes, a operação segue no servidor. No `start`, o container nasce sem
ninguém esperando: a tentativa vira falha transitória com o executor de pé, e o claim seguinte
encontra a área ocupada (`work_area_dirty`). No `remove`, a thread continua na fila do lock (que
não é FIFO), e a manutenção, ao repetir, empilha outras atrás dela.

O fechamento definitivo é do servidor: tomar o lock com prazo e responder 503 `busy`. Isso exige
reinstalar o executor-ctl no host e fica para uma frente própria.
"""

import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from executor_ctl.docker_cli import COMMAND_TIMEOUT_S, ContainerStatus, DockerCli, State
from executor_ctl.service import ExecutorService
from goblinshift.runs.worker import executor_ctl_runner as client

from .conftest import make_config

ATTEMPT = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abcd")
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64


class CountingDocker(DockerCli):
    """Docker sem processo: devolve o estado pedido pelo teste e conta cada chamada ao CLI."""

    def __init__(self, state: State) -> None:
        super().__init__(binary="docker-que-nao-roda")
        self.state: State = state
        self.calls: list[str] = []

    def inspect(self, name: str) -> ContainerStatus:
        self.calls.append("inspect")
        if self.state == "absent":
            return ContainerStatus.absent()
        exit_code = 0 if self.state == "exited" else None
        return ContainerStatus(self.state, exit_code, False, None, None)

    def run(self, arguments: list[str]) -> None:
        self.calls.append("run")
        self.state = "running"

    def remove(self, name: str) -> None:
        self.calls.append("rm")
        self.state = "absent"


def service_with(tmp_path: Path, docker: DockerCli) -> ExecutorService:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = make_config(tmp_path, tmp_path, allowed=[IMAGE])
    (config.work_area / str(ATTEMPT) / "inputs").mkdir(parents=True)
    return ExecutorService(config, docker, os.getgid())


def start_calls(tmp_path: Path) -> list[str]:
    """Chamadas do `start` no pior caminho: container ausente, criado agora."""
    docker = CountingDocker("absent")
    service_with(tmp_path, docker).start(ATTEMPT, IMAGE)
    return docker.calls


def remove_calls(tmp_path: Path) -> list[str]:
    """Chamadas do `remove` no pior caminho: container parado, removido agora."""
    docker = CountingDocker("exited")
    service_with(tmp_path, docker).remove(ATTEMPT)
    return docker.calls


def test_prazo_do_cli_do_docker_espelhado_no_cliente() -> None:
    assert timedelta(seconds=COMMAND_TIMEOUT_S) == client.CTL_DOCKER_CALL_TIMEOUT


@pytest.mark.usefixtures("synthetic_policy_metadata")
def test_chamadas_do_start_espelhadas_no_cliente(tmp_path: Path) -> None:
    calls = start_calls(tmp_path)

    assert calls == ["inspect", "run", "inspect"]
    assert len(calls) == client.CTL_START_DOCKER_CALLS


@pytest.mark.usefixtures("synthetic_policy_metadata")
def test_chamadas_do_remove_espelhadas_no_cliente(tmp_path: Path) -> None:
    calls = remove_calls(tmp_path)

    assert calls == ["inspect", "rm"]
    assert len(calls) == client.CTL_REMOVE_DOCKER_CALLS


@pytest.mark.usefixtures("synthetic_policy_metadata")
def test_quem_segura_o_lock_da_area_cabe_no_espelho_do_cliente(tmp_path: Path) -> None:
    # Start e remove tomam o lock da área; quem chega espera, no pior caso, a operação inteira
    # de quem está na frente.
    longest = max(len(start_calls(tmp_path / "start")), len(remove_calls(tmp_path / "remove")))

    assert longest == client.CTL_LOCK_HOLDER_DOCKER_CALLS


def lock_wait_calls(tmp_path: Path) -> int:
    """Chamadas da operação mais longa que pode estar à frente no lock, medidas no serviço."""
    return max(len(start_calls(tmp_path / "start")), len(remove_calls(tmp_path / "remove")))


@pytest.mark.usefixtures("synthetic_policy_metadata")
def test_cliente_espera_mais_que_o_pior_caso_do_start_no_servidor(tmp_path: Path) -> None:
    # Medido no serviço, não no espelho: o próprio start e uma operação inteira à frente no lock.
    own = len(start_calls(tmp_path / "own"))
    server_ceiling = timedelta(seconds=COMMAND_TIMEOUT_S * (own + lock_wait_calls(tmp_path)))

    # Literal de propósito, calculado à mão ((3 + 3) x 30 s): uma mudança no custo do serviço
    # aparece aqui como diff explícito, e não só como folga menor na desigualdade abaixo.
    assert server_ceiling == timedelta(seconds=180)
    assert server_ceiling < client.START_TIMEOUT


@pytest.mark.usefixtures("synthetic_policy_metadata")
def test_cliente_espera_mais_que_o_pior_caso_do_remove_no_servidor(tmp_path: Path) -> None:
    # Medido no serviço: o próprio remove (sem o rmtree, que é a margem) e um start à frente.
    own = len(remove_calls(tmp_path / "own"))
    server_ceiling = timedelta(seconds=COMMAND_TIMEOUT_S * (own + lock_wait_calls(tmp_path)))

    # Literal de propósito, calculado à mão ((2 + 3) x 30 s), pelo mesmo motivo do start.
    assert server_ceiling == timedelta(seconds=150)
    assert server_ceiling < client.REMOVE_TIMEOUT
