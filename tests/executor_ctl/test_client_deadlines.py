"""Contrato de prazo de `start`, `stop` e `remove`: o worker espera o pior caso do executor-ctl.

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

import configparser
import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from executor_ctl.docker_cli import COMMAND_TIMEOUT_S, DockerCli
from executor_ctl.service import AREA_LOCK_WAIT_S, ExecutorService
from goblinshift.runs.worker import executor_ctl_runner as client

from .conftest import make_config
from .counting_docker import CountingDocker
from .installer_copy import installer_copy

ATTEMPT = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abcd")
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64


def service_with(tmp_path: Path, docker: DockerCli) -> ExecutorService:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = make_config(tmp_path, tmp_path, allowed=[IMAGE])
    (config.work_area / str(ATTEMPT) / "inputs").mkdir(parents=True)
    return ExecutorService(config, docker, os.getgid())


def start_calls(tmp_path: Path) -> list[str]:
    """Chamadas do `start` no pior caminho: container ausente, criado agora."""
    docker = CountingDocker("absent", ATTEMPT)
    service_with(tmp_path, docker).start(ATTEMPT, IMAGE)
    return docker.calls


def remove_calls(tmp_path: Path) -> list[str]:
    """Chamadas do `remove` no pior caminho: container parado, removido agora."""
    docker = CountingDocker("exited", ATTEMPT)
    service_with(tmp_path, docker).remove(ATTEMPT)
    return docker.calls


def stop_calls(tmp_path: Path) -> list[str]:
    """Chamadas do `stop` no pior caminho: container que nunca iniciou, removido no stop."""
    docker = CountingDocker("created", ATTEMPT)
    service_with(tmp_path, docker).stop(ATTEMPT, 0)
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


@pytest.mark.usefixtures("synthetic_policy_metadata")
def test_chamadas_do_stop_espelhadas_no_cliente(tmp_path: Path) -> None:
    calls = stop_calls(tmp_path)

    assert calls == ["inspect", "stop", "inspect", "inspect", "rm"]
    assert len(calls) == client.CTL_STOP_DOCKER_CALLS


@pytest.mark.usefixtures("synthetic_policy_metadata")
def test_stop_do_cliente_espera_o_pior_caso_do_servico(tmp_path: Path) -> None:
    # Medido no serviço: as chamadas do próprio stop e a espera do lock da área antes do rm do
    # container que nunca iniciou (o stop não espera uma operação inteira: lock ocupado devolve
    # o que leu). A carência soma dos dois lados e fica fora da conta.
    own = len(stop_calls(tmp_path))
    server_ceiling = timedelta(seconds=COMMAND_TIMEOUT_S * own + AREA_LOCK_WAIT_S)

    # Literal de propósito, calculado à mão (5 x 30 s + 5 s de lock), pelo mesmo motivo do start.
    assert server_ceiling == timedelta(seconds=155)
    assert server_ceiling < client.STOP_BASE_TIMEOUT


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


def test_unit_gerada_tem_killmode_mixed_e_prazo_acima_da_operacao_mais_longa(
    tmp_path: Path,
) -> None:
    """O restart só sinaliza o processo principal, e o systemd espera a operação mais longa.

    Sem `KillMode=mixed`, o SIGTERM vai ao cgroup inteiro e mata o `docker` em voo; com prazo
    menor que o pior caso, o SIGKILL chega antes de o servidor responder o que já aceitou.
    """
    with installer_copy(tmp_path) as copy:
        result = copy.run()
        assert result.returncode == 0, result.stderr
        unit = configparser.ConfigParser(interpolation=None, strict=False)
        unit.optionxform = str  # type: ignore[assignment,method-assign]
        unit.read_string(copy.unit.read_text())

    service = unit["Service"]
    assert service["KillMode"] == "mixed"
    stop_timeout = timedelta(seconds=int(service["TimeoutStopSec"]))
    assert stop_timeout > client.START_TIMEOUT
    assert stop_timeout > client.REMOVE_TIMEOUT
    # Stop no servidor, calculado à mão: 4 chamadas de 30 s, o stop com grace máximo (40 s),
    # 5 s de lock e 10 s de leitura do pedido.
    assert stop_timeout > timedelta(seconds=4 * 30 + 40 + 5 + 10)
