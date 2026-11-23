"""Lock da área com prazo: quem chega atrás de uma operação travada desiste, não empilha."""

import os
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from executor_ctl.docker_cli import (
    ContainerNameConflictError,
    ContainerRunningError,
    ContainerStatus,
    DockerCli,
    DockerUnavailableError,
    State,
)
from executor_ctl.profile import IoLimits
from executor_ctl.service import (
    AREA_LOCK_WAIT_S,
    AreaBusyError,
    ExecutorService,
    ForeignContainerError,
)
from executor_ctl.workarea import WorkAreaDirtyError

from .conftest import container_id, make_config, owned_status
from .counting_docker import CountingDocker
from .labeled_docker import CONTAINER_ID, LabeledDocker
from .stuck_docker import StuckDocker, holding_area

pytestmark = [pytest.mark.host_python, pytest.mark.usefixtures("synthetic_policy_metadata")]

HOLDER = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abcd")
LATECOMER = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abce")
OTHER_ATTEMPT = uuid.UUID("0193a3f0-7b2c-7def-8a01-23456789abcf")
HOLDER_NAME = f"gs-exec-{HOLDER}"
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
OTHER_IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "f" * 64
# Curto para o teste; o de produção é AREA_LOCK_WAIT_S.
TEST_LOCK_WAIT_S = 0.2
# Prazo do lock nos testes do `stop`, que só desiste dele quando a área está ocupada.
STOP_LOCK_WAIT_S = 0.05
# Folga para o agendador da máquina de CI; ainda bem abaixo do prazo de produção.
SCHEDULING_SLACK_S = 2.0


def service_with(tmp_path: Path, docker: DockerCli, *, lock_wait_s: float) -> ExecutorService:
    config = make_config(tmp_path, tmp_path, allowed=[IMAGE])
    return ExecutorService(config, docker, os.getgid(), area_lock_wait_s=lock_wait_s)


def test_prazo_de_producao_do_lock_e_curto_e_positivo() -> None:
    # Literal de propósito: mudar o prazo do contrato aparece aqui como diff explícito.
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


class FailingDocker(DockerCli):
    """Docker sem processo: falha uma vez como o motivo pede, depois responde ausente."""

    def __init__(self, failure: str) -> None:
        super().__init__(binary="docker-que-nao-roda")
        self.failure = failure
        self.calls: list[str] = []

    def inspect(self, name: str) -> ContainerStatus:
        self.calls.append(f"inspect {name}")
        failure, self.failure = self.failure, ""
        if failure == "docker_unavailable":
            raise DockerUnavailableError("daemon fora")
        if failure == "still_running":
            return owned_status("running", HOLDER)
        return ContainerStatus.absent()

    def run(self, arguments: list[str]) -> None:
        self.calls.append("run")

    def remove(self, name: str) -> None:
        self.calls.append(f"rm {name}")


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        ("still_running", ContainerRunningError),
        ("docker_unavailable", DockerUnavailableError),
        ("work_area_dirty", WorkAreaDirtyError),
    ],
)
def test_operacao_que_falha_dentro_do_lock_o_libera(
    tmp_path: Path, failure: str, error: type[Exception]
) -> None:
    docker = FailingDocker(failure)
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)
    if failure == "work_area_dirty":
        (tmp_path / "exec-work" / str(HOLDER) / "inputs").mkdir(parents=True)
        (tmp_path / "exec-work" / "sobra-de-outra-tentativa").mkdir()

    with pytest.raises(error):
        if failure == "work_area_dirty":
            service.start(HOLDER, IMAGE)
        else:
            service.remove(HOLDER)

    # Lock preso pela falha faria esta chamada esperar o prazo e sair com AreaBusyError.
    service.remove(LATECOMER)

    assert docker.calls[-1] == f"rm gs-exec-{LATECOMER}"


def test_stop_de_container_created_remove_e_responde_absent(tmp_path: Path) -> None:
    # O runtime falhou e o `docker run --detach` deixou o container em `created`: sem o rm, ele
    # seguiria aparecendo como existente e a área nunca seria liberada.
    docker = CountingDocker("created", HOLDER)
    service = service_with(tmp_path, docker, lock_wait_s=STOP_LOCK_WAIT_S)

    status = service.stop(HOLDER, 0)

    assert status.state == "absent"
    assert docker.calls == ["inspect", "stop", "inspect", "inspect", "rm"]


def test_stop_de_container_rodando_nao_remove(tmp_path: Path) -> None:
    docker = CountingDocker("running", HOLDER)
    service = service_with(tmp_path, docker, lock_wait_s=STOP_LOCK_WAIT_S)

    status = service.stop(HOLDER, 0)

    assert status.state == "exited"
    assert docker.calls == ["inspect", "stop", "inspect"]


class CreatedThenStartedDocker(CountingDocker):
    """Depois do stop, o `inspect` vê `created`; antes da releitura, um start o iniciou."""

    def inspect(self, name: str) -> ContainerStatus:
        status = super().inspect(name)
        if self.state == "created" and "stop" in self.calls:
            self.state = "running"
        return status


def test_stop_reinspeciona_sob_o_lock(tmp_path: Path) -> None:
    docker = CreatedThenStartedDocker("created", HOLDER)
    service = service_with(tmp_path, docker, lock_wait_s=STOP_LOCK_WAIT_S)

    status = service.stop(HOLDER, 0)

    assert status.state == "running"
    assert docker.calls == ["inspect", "stop", "inspect", "inspect"]


class CreatedWhileStuckDocker(StuckDocker):
    """Docker travado no primeiro `inspect`, como o de um start em curso, que vê `created`."""

    def inspect(self, name: str) -> ContainerStatus:
        super().inspect(name)
        return owned_status("created", HOLDER)

    def stop(self, name: str, grace_s: int) -> None:
        self.calls.append(f"stop {name}")


def test_stop_nao_remove_created_com_start_em_curso(tmp_path: Path) -> None:
    # O start em curso segura o lock da área: o `created` que o stop lê pode ser o container que
    # o `run` acabou de criar e ainda vai iniciar. Removê-lo agora derrubaria a tentativa.
    docker = CreatedWhileStuckDocker()
    service = service_with(tmp_path, docker, lock_wait_s=STOP_LOCK_WAIT_S)

    with holding_area(docker, lambda: service.start(HOLDER, IMAGE)):
        status = service.stop(HOLDER, 0)

    assert status.state == "created"
    # Lista exata: o `rm` seria gravado como "rm <alvo>", e só ela prova que não houve remoção.
    # A primeira é o inspect do start travado; o stop lê pelo nome e age pelo `Id` lido.
    target = container_id(HOLDER)
    assert docker.calls == [
        f"inspect {HOLDER_NAME}",
        f"inspect {HOLDER_NAME}",
        f"stop {target}",
        f"inspect {target}",
    ]


class RecordingDocker(CountingDocker):
    """Guarda os argumentos do `docker run` para conferir o perfil que o serviço montou."""

    def __init__(self) -> None:
        super().__init__("absent", HOLDER)
        self.run_arguments: list[str] = []

    def run(self, arguments: list[str]) -> None:
        self.run_arguments = arguments
        super().run(arguments)


def test_io_limits_da_config_vai_ao_docker_run_no_dispositivo_da_area(tmp_path: Path) -> None:
    limits = IoLimits(read_bps=104857600, write_bps=52428800, read_iops=2000, write_iops=1000)
    config = replace(make_config(tmp_path, tmp_path, allowed=[IMAGE]), io_limits=limits)
    docker = RecordingDocker()
    area_device = os.stat(config.work_area).st_dev
    device = f"/dev/block/{os.major(area_device)}:{os.minor(area_device)}"
    (config.work_area / str(HOLDER) / "inputs").mkdir(parents=True)

    ExecutorService(config, docker, os.getgid()).start(HOLDER, IMAGE)

    assert f"--device-write-bps={device}:52428800" in docker.run_arguments
    assert f"--device-read-iops={device}:2000" in docker.run_arguments


def test_sem_io_limits_o_servico_nao_passa_dispositivo(tmp_path: Path) -> None:
    docker = RecordingDocker()
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)
    (tmp_path / "exec-work" / str(HOLDER) / "inputs").mkdir(parents=True)

    service.start(HOLDER, IMAGE)

    assert docker.run_arguments
    assert not [arg for arg in docker.run_arguments if arg.startswith("--device")]


def run_operation(service: ExecutorService, operation: str) -> None:
    if operation == "start":
        service.start(HOLDER, IMAGE)
    elif operation == "status":
        service.status(HOLDER)
    elif operation == "stop":
        service.stop(HOLDER, 0)
    else:
        service.remove(HOLDER)


@pytest.mark.parametrize("label", [str(OTHER_ATTEMPT), None], ids=["outra_tentativa", "sem_label"])
@pytest.mark.parametrize("operation", ["start", "status", "stop", "remove"])
def test_conteiner_alheio_e_recusado_nas_quatro_operacoes(
    tmp_path: Path, operation: str, label: str | None
) -> None:
    # Outro projeto do host, ou um engano de operador, deu ao container o nome da tentativa.
    docker = LabeledDocker("exited", label, IMAGE)
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)
    work_dir = tmp_path / "exec-work" / str(HOLDER)
    (work_dir / "inputs").mkdir(parents=True)

    with pytest.raises(ForeignContainerError):
        run_operation(service, operation)

    assert docker.calls == [f"inspect {HOLDER_NAME}"]
    assert (work_dir / "inputs").is_dir()


def test_start_nao_adota_conteiner_da_tentativa_com_outra_imagem(tmp_path: Path) -> None:
    docker = LabeledDocker("running", str(HOLDER), OTHER_IMAGE)
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)

    with pytest.raises(ForeignContainerError):
        service.start(HOLDER, IMAGE)

    assert docker.calls == [f"inspect {HOLDER_NAME}"]


def test_start_repetido_adota_o_proprio_conteiner(tmp_path: Path) -> None:
    docker = LabeledDocker("running", str(HOLDER), IMAGE)
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)

    status = service.start(HOLDER, IMAGE)

    assert status.state == "running"
    assert docker.calls == [f"inspect {HOLDER_NAME}"]


@pytest.mark.parametrize(
    ("operation", "state", "expected"),
    [
        (
            "stop",
            "running",
            [f"inspect {HOLDER_NAME}", f"stop {CONTAINER_ID}", f"inspect {CONTAINER_ID}"],
        ),
        ("remove", "exited", [f"inspect {HOLDER_NAME}", f"rm {CONTAINER_ID}"]),
    ],
)
def test_stop_e_remove_agem_pelo_id_do_conteiner_inspecionado(
    tmp_path: Path, operation: str, state: State, expected: list[str]
) -> None:
    # Entre o inspect e o stop/rm, o nome pode passar a outro container; o `Id` não muda de dono.
    docker = LabeledDocker(state, str(HOLDER), IMAGE)
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)

    run_operation(service, operation)

    assert docker.calls == expected


class NameTakenOnRunDocker(LabeledDocker):
    """Entre o inspect e o run do start, um container alheio tomou o nome da tentativa."""

    def run(self, arguments: list[str]) -> None:
        self.calls.append("run")
        self.state = "running"
        raise ContainerNameConflictError("Conflict. The container name is already in use")


def test_corrida_no_start_com_nome_tomado_por_alheio_e_recusada(tmp_path: Path) -> None:
    docker = NameTakenOnRunDocker("absent", None, IMAGE)
    service = service_with(tmp_path, docker, lock_wait_s=TEST_LOCK_WAIT_S)
    (tmp_path / "exec-work" / str(HOLDER) / "inputs").mkdir(parents=True)

    with pytest.raises(ForeignContainerError):
        service.start(HOLDER, IMAGE)

    assert docker.calls == [f"inspect {HOLDER_NAME}", "run", f"inspect {HOLDER_NAME}"]
