"""executor-ctl com Docker real: as quatro operações sobre containers de verdade.

O runtime vem de GS_TEST_RUNTIME (padrão runc, o que existe no Mac); a CI roda também com
runsc, o runtime de produção. A imagem de teste é construída aqui, com um executor de mentira
(fake_executor.py) no lugar do real, e entra na allowlist pelo digest.
"""

import json
import os
import shutil
import stat
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from executor_ctl.client import Response, call
from executor_ctl.docker_cli import DockerCli
from executor_ctl.profile import EXECUTOR_UID, container_name

from .conftest import make_config, running_server

pytestmark = pytest.mark.executor_ctl

RUNTIME = os.environ.get("GS_TEST_RUNTIME", "runc")
BASE_IMAGE = (
    "python:3.14.8-slim-trixie"
    "@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170"
)
TEST_IMAGE_TAG = "goblinshift-executor-ctl-test:latest"
# Só para o Docker sem o image store do containerd, em que imagem construída não tem digest.
REGISTRY_IMAGE = "registry:3.0.0"
FAKE_EXECUTOR = Path(__file__).with_name("fake_executor.py")
WAIT_TIMEOUT_S = 30.0
POLL_S = 0.1
STOP_GRACE_S = 1
# O que o stop pode levar além da carência: SIGKILL, saída do runtime e o inspect.
STOP_MARGIN_S = 5.0


def docker(*arguments: str) -> str:
    return subprocess.run(
        ["docker", *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture(scope="session")
def executor_image(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Imagem de teste fixada por digest, como o deploy grava na allowlist."""
    if shutil.which("docker") is None:
        pytest.skip("docker não está instalado")
    try:
        docker("info", "--format", "{{.ServerVersion}}")
    except subprocess.CalledProcessError:
        pytest.skip("o daemon do Docker não responde")
    context = tmp_path_factory.mktemp("imagem")
    shutil.copy(FAKE_EXECUTOR, context / "fake_executor.py")
    (context / "Dockerfile").write_text(
        f"FROM {BASE_IMAGE}\n"
        "COPY fake_executor.py /opt/fake_executor.py\n"
        'ENTRYPOINT ["python", "/opt/fake_executor.py", "/work"]\n'
    )
    docker("build", "--quiet", "--tag", TEST_IMAGE_TAG, str(context))
    return _reference_by_digest(TEST_IMAGE_TAG)


def _reference_by_digest(tag: str) -> str:
    digests = json.loads(docker("image", "inspect", "--format", "{{json .RepoDigests}}", tag))
    if digests:
        return str(digests[0])
    return _push_to_ephemeral_registry(tag)


def _push_to_ephemeral_registry(tag: str) -> str:
    """Sem o image store do containerd, o digest só existe depois de um push."""
    registry = docker("run", "--detach", "--rm", "--publish=127.0.0.1::5000", REGISTRY_IMAGE)
    try:
        address = docker("port", registry, "5000").splitlines()[0]
        pushed = f"{address}/{tag}"
        docker("tag", tag, pushed)
        docker("push", "--quiet", pushed)
        digests = json.loads(
            docker("image", "inspect", "--format", "{{json .RepoDigests}}", pushed)
        )
    finally:
        docker("rm", "--force", registry)
    return str(digests[0])


@dataclass(frozen=True)
class Ctl:
    socket: Path
    area: Path
    image: str
    group_id: int

    def call(
        self, method: str, attempt_id: uuid.UUID | str, action: str = "", body: object = None
    ) -> Response:
        return call(self.socket, method, f"/v1/attempts/{attempt_id}{action}", body)

    def start(self, attempt_id: uuid.UUID, image: str | None = None) -> Response:
        return self.call("POST", attempt_id, "/start", {"image": image or self.image})

    def prepare(self, mode: str) -> uuid.UUID:
        """Faz o papel do worker: <area>/<id> com job.json e inputs/."""
        attempt_id = uuid.uuid4()
        work_dir = self.area / str(attempt_id)
        inputs = work_dir / "inputs"
        inputs.mkdir(parents=True)
        for directory, mode_bits in ((work_dir, 0o2770), (inputs, 0o2750)):
            directory.chmod(mode_bits)
        job = work_dir / "job.json"
        job.write_text(json.dumps({"mode": mode}))
        job.chmod(0o640)
        return attempt_id

    def wait_for(self, attempt_id: uuid.UUID, state: str) -> dict[str, Any]:
        deadline = time.monotonic() + WAIT_TIMEOUT_S
        while True:
            response = self.call("GET", attempt_id)
            assert response.status == 200, response.body
            if response.body["state"] == state:
                return response.body
            assert time.monotonic() < deadline, f"não chegou a {state}: {response.body}"
            time.sleep(POLL_S)


@pytest.fixture
def ctl(executor_image: str, tmp_path: Path, socket_dir: Path) -> Iterator[Ctl]:
    config = make_config(tmp_path, socket_dir, runtime=RUNTIME, allowed=[executor_image])
    with running_server(config, DockerCli()) as socket:
        group_id = os.getgid()
        yield Ctl(socket, config.work_area, executor_image, group_id)
    for entry in config.work_area.iterdir():
        subprocess.run(
            ["docker", "rm", "--force", f"gs-exec-{entry.name}"], check=False, capture_output=True
        )


def containers_of(attempt_id: uuid.UUID) -> list[str]:
    return docker(
        "ps", "--all", "--quiet", f"--filter=label=goblinshift.attempt={attempt_id}"
    ).split()


def test_start_roda_o_executor_e_deixa_a_saida_em_out(ctl: Ctl) -> None:
    attempt_id = ctl.prepare("write")

    response = ctl.start(attempt_id)
    final = ctl.wait_for(attempt_id, "exited")

    assert response.status == 200
    assert response.body["state"] in {"running", "exited"}
    assert final["exit_code"] == 0
    assert final["oom_killed"] is False
    assert final["started_at"] is not None
    assert final["finished_at"] is not None
    work_dir = ctl.area / str(attempt_id)
    assert (work_dir / "out" / "result.txt").read_text() == "ok\n"
    assert (work_dir / "progress.jsonl").exists()


def test_container_sobe_com_o_perfil_fixo(ctl: Ctl) -> None:
    attempt_id = ctl.prepare("write")

    ctl.start(attempt_id)
    [info] = json.loads(docker("inspect", container_name(attempt_id)))

    host_config = info["HostConfig"]
    assert host_config["Runtime"] == RUNTIME
    assert host_config["NetworkMode"] == "none"
    assert host_config["ReadonlyRootfs"] is True
    assert host_config["CapDrop"] == ["ALL"]
    assert info["Config"]["User"] == f"{EXECUTOR_UID}:{ctl.group_id}"
    assert info["Config"]["Image"] == ctl.image
    mounts = {mount["Destination"]: mount["RW"] for mount in info["Mounts"]}
    assert mounts == {"/work": True, "/work/inputs": False}


def test_start_repetido_nao_cria_segundo_container(ctl: Ctl) -> None:
    attempt_id = ctl.prepare("sleep")

    first = ctl.start(attempt_id)
    second = ctl.start(attempt_id)

    assert (first.status, second.status) == (200, 200)
    assert second.body["state"] == "running"
    assert len(containers_of(attempt_id)) == 1


def test_status_sem_container_e_absent(ctl: Ctl) -> None:
    response = ctl.call("GET", uuid.uuid4())

    assert response.status == 200
    assert response.body == {
        "state": "absent",
        "exit_code": None,
        "oom_killed": False,
        "started_at": None,
        "finished_at": None,
    }


def test_stop_encerra_o_container_dentro_da_carencia(ctl: Ctl) -> None:
    attempt_id = ctl.prepare("sleep")
    ctl.start(attempt_id)
    ctl.wait_for(attempt_id, "running")

    started = time.monotonic()
    response = ctl.call("POST", attempt_id, "/stop", {"grace_s": STOP_GRACE_S})
    elapsed = time.monotonic() - started

    assert response.status == 200
    assert response.body["state"] == "exited"
    assert response.body["exit_code"] is not None
    assert elapsed <= STOP_GRACE_S + STOP_MARGIN_S


def test_stop_sem_container_e_sucesso(ctl: Ctl) -> None:
    response = ctl.call("POST", uuid.uuid4(), "/stop", {"grace_s": 0})

    assert response.status == 200
    assert response.body["state"] == "absent"


def test_remove_apaga_container_e_area_e_pode_repetir(ctl: Ctl) -> None:
    attempt_id = ctl.prepare("write")
    ctl.start(attempt_id)
    ctl.wait_for(attempt_id, "exited")
    work_dir = ctl.area / str(attempt_id)
    # O arquivo do executor pertence ao UID 61000 no Linux; quem roda o teste não é o dono.
    assert (work_dir / "out" / "result.txt").exists()

    first = ctl.call("DELETE", attempt_id)
    second = ctl.call("DELETE", attempt_id)

    assert (first.status, second.status) == (204, 204)
    assert not work_dir.exists()
    assert containers_of(attempt_id) == []
    assert ctl.call("GET", attempt_id).body["state"] == "absent"


def test_remove_com_container_rodando_da_409_e_nao_apaga_nada(ctl: Ctl) -> None:
    attempt_id = ctl.prepare("sleep")
    ctl.start(attempt_id)
    ctl.wait_for(attempt_id, "running")

    response = ctl.call("DELETE", attempt_id)

    assert response.status == 409
    assert response.body["error"] == "still_running"
    assert (ctl.area / str(attempt_id) / "job.json").exists()
    assert len(containers_of(attempt_id)) == 1


def test_imagem_fora_da_allowlist_da_403_sem_criar_container(ctl: Ctl) -> None:
    attempt_id = ctl.prepare("write")
    other = ctl.image.split("@")[0] + "@sha256:" + "0" * 64

    response = ctl.start(attempt_id, image=other)

    assert response.status == 403
    assert response.body["error"] == "image_not_allowed"
    assert containers_of(attempt_id) == []


def test_area_suja_da_409_sem_criar_container(ctl: Ctl) -> None:
    ctl.prepare("write")
    attempt_id = ctl.prepare("write")

    response = ctl.start(attempt_id)

    assert response.status == 409
    assert response.body["error"] == "work_area_dirty"
    assert containers_of(attempt_id) == []


def test_diretorio_da_tentativa_ausente_da_409(ctl: Ctl) -> None:
    attempt_id = uuid.uuid4()

    response = ctl.start(attempt_id)

    assert response.status == 409
    assert response.body["error"] == "work_dir_missing"
    assert containers_of(attempt_id) == []


def test_attempt_id_invalido_da_400(ctl: Ctl) -> None:
    response = ctl.call("GET", "gs-exec-x;docker-ps")

    assert response.status == 400
    assert response.body["error"] == "invalid_attempt_id"


def test_executor_nao_tem_rede(ctl: Ctl) -> None:
    attempt_id = ctl.prepare("network")
    ctl.start(attempt_id)
    final = ctl.wait_for(attempt_id, "exited")

    report = json.loads((ctl.area / str(attempt_id) / "out" / "network.json").read_text())

    assert final["exit_code"] == 0
    # Sob runc o namespace vazio ainda lista túneis desligados do kernel (gre0, sit0...);
    # sob runsc só há lo. Em nenhum dos dois existe interface ethernet.
    assert "lo" in report["interfaces"]
    assert not [name for name in report["interfaces"] if name.startswith("eth")]
    assert report["outbound_error"] is not None
    assert report["dns_error"] is not None


@pytest.mark.parametrize("swapped", ["attempt", "inputs", "out"])
def test_symlink_na_area_da_409_sem_tocar_fora_dela(ctl: Ctl, tmp_path: Path, swapped: str) -> None:
    attempt_id = ctl.prepare("write")
    work_dir = ctl.area / str(attempt_id)
    outside = tmp_path / "fora"
    (outside / "inputs").mkdir(parents=True)
    outside.chmod(0o700)
    if swapped == "attempt":
        shutil.rmtree(work_dir)
        work_dir.symlink_to(outside)
    else:
        target = work_dir / swapped
        if target.exists():
            target.rmdir()
        target.symlink_to(outside)

    response = ctl.start(attempt_id)

    assert response.status == 409
    assert response.body["error"] == "work_dir_missing"
    assert containers_of(attempt_id) == []
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700
    assert sorted(path.name for path in outside.iterdir()) == ["inputs"]


def test_remove_com_symlink_para_fora_nao_apaga_o_alvo(ctl: Ctl, tmp_path: Path) -> None:
    attempt_id = ctl.prepare("write")
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "importante.txt").write_text("fica")
    (ctl.area / str(attempt_id) / "out").symlink_to(outside)

    response = ctl.call("DELETE", attempt_id)

    assert response.status == 204
    assert not (ctl.area / str(attempt_id)).exists()
    assert (outside / "importante.txt").read_text() == "fica"
