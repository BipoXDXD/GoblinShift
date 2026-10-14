"""Fronteiras do Compose com o host: Docker, `executor-ctl` e Runner (ADR 004, fatia 4)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = sorted([*ROOT.glob("compose*.yaml"), ROOT / "deploy" / "edge" / "compose.yaml"])
CTL_DIR = "/run/goblinshift"
EXECUTOR_CTL_CLIENTS = {"worker", "maintenance"}
EXECUTOR_GROUP = "61001"


def services(compose_file: Path) -> dict[str, dict[str, Any]]:
    document = yaml.safe_load(compose_file.read_text())
    found: dict[str, dict[str, Any]] = document.get("services") or {}
    return found


def volume_targets(service: dict[str, Any]) -> list[str]:
    targets = []
    for volume in service.get("volumes", []):
        if isinstance(volume, str):
            targets.append(volume.split(":")[1])
        else:
            targets.append(volume["target"])
    return targets


def test_ha_arquivos_compose_para_verificar() -> None:
    assert len(COMPOSE_FILES) >= 4


@pytest.mark.parametrize(
    "compose_file", COMPOSE_FILES, ids=lambda path: str(path.relative_to(ROOT))
)
def test_nenhum_compose_monta_o_socket_do_docker(compose_file: Path) -> None:
    # Quem controla o daemon do Docker tem root no host (ADR 004); só o executor-ctl o acessa.
    assert "docker.sock" not in compose_file.read_text()


@pytest.mark.parametrize(
    "compose_file", COMPOSE_FILES, ids=lambda path: str(path.relative_to(ROOT))
)
def test_so_worker_e_manutencao_montam_o_socket_do_executor_ctl(compose_file: Path) -> None:
    mounting = {
        name
        for name, service in services(compose_file).items()
        if any(target.startswith(CTL_DIR) for target in volume_targets(service))
    }

    assert mounting <= EXECUTOR_CTL_CLIENTS


@pytest.mark.parametrize(
    "compose_file", COMPOSE_FILES, ids=lambda path: str(path.relative_to(ROOT))
)
def test_a_confirmacao_do_runner_local_nao_existe_mais(compose_file: Path) -> None:
    assert "GS_ALLOW_LOCAL_RUNNER" not in compose_file.read_text()


def test_producao_usa_o_executor_ctl_com_a_imagem_obrigatoria() -> None:
    production = services(ROOT / "compose.prod.yaml")

    for name in ("api", *sorted(EXECUTOR_CTL_CLIENTS)):
        environment = production[name]["environment"]
        assert environment["GS_RUNNER"] == "executor_ctl", name
        assert environment["GS_EXECUTOR_IMAGE"].startswith("${GS_EXECUTOR_IMAGE:?"), name


@pytest.mark.parametrize("name", sorted(EXECUTOR_CTL_CLIENTS))
def test_producao_monta_socket_e_area_de_trabalho_com_o_grupo_do_executor(name: str) -> None:
    service = services(ROOT / "compose.prod.yaml")[name]

    assert service["group_add"] == [EXECUTOR_GROUP]
    assert CTL_DIR in volume_targets(service)
    work_root = service["environment"]["GS_WORK_ROOT"]
    assert work_root in volume_targets(service)


def test_a_base_nao_compartilha_pids_nem_escolhe_o_runner() -> None:
    # O Runner (e o que ele exige do container) é de cada ambiente: local no override de
    # desenvolvimento, executor-ctl em produção.
    base = services(ROOT / "compose.yaml")

    for name in sorted(EXECUTOR_CTL_CLIENTS):
        assert "pid" not in base[name]
        assert "GS_RUNNER" not in base[name]["environment"]


def test_desenvolvimento_continua_com_o_runner_local() -> None:
    development = services(ROOT / "compose.override.yaml")

    for name in sorted(EXECUTOR_CTL_CLIENTS):
        assert development[name]["environment"]["GS_RUNNER"] == "local"
    assert development["worker"]["init"] is True
