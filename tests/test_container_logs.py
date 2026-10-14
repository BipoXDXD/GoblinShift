"""Logs dos containers de produção: sem log de acesso do Uvicorn e com rotação por serviço.

A VPS é compartilhada (docs/GoblinShiftPlan.md, 3.8): a rotação fica no Compose, por serviço,
nunca no /etc/docker/daemon.json, que vale para todos os projetos do host.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "compose.yaml"
PROD = ROOT / "compose.prod.yaml"
DOCKERFILE = ROOT / "docker" / "app.Dockerfile"
MIB = 1024 * 1024
# Teto por serviço que cobre ao menos 14 dias de log no volume previsto (plano, 1.7).
MIN_MIB_PER_SERVICE = 50
# Parte da raiz (sistema, imagens e o outro projeto, plano 3.8) que os logs podem ocupar.
MAX_MIB_ALL_SERVICES = 512
SIZE_UNITS = {"k": 1024, "m": MIB, "g": 1024 * MIB}


def _services(path: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text())["services"]
    return found


def _production_services() -> dict[str, dict[str, Any]]:
    merged = {name: dict(service) for name, service in _services(BASE).items()}
    for name, override in _services(PROD).items():
        merged.setdefault(name, {}).update(override)
    return merged


def _retained_bytes(logging: dict[str, Any]) -> int:
    options = logging["options"]
    size = re.fullmatch(r"(\d+)([kmg])", options["max-size"])
    assert size, options["max-size"]
    return int(size[1]) * SIZE_UNITS[size[2]] * int(options["max-file"])


def test_uvicorn_roda_sem_log_de_acesso() -> None:
    # O evento amplo `main` já registra cada requisição, sem IP completo (plano, 3.7).
    cmd_line = next(
        line for line in DOCKERFILE.read_text().split("\nCMD ")[1:] if "uvicorn" in line
    )
    cmd = json.loads(cmd_line.replace("\\\n", ""))
    assert cmd[0] == "uvicorn"
    assert "--no-access-log" in cmd


@pytest.mark.parametrize("name", sorted(_production_services()))
def test_todo_servico_de_producao_tem_rotacao_de_log(name: str) -> None:
    logging = _production_services()[name].get("logging")
    assert logging, f"{name} sem `logging`: cairia no padrão global do Docker do host"
    assert logging["driver"] == "local"
    assert _retained_bytes(logging) >= MIN_MIB_PER_SERVICE * MIB


def test_logs_retidos_cabem_no_orcamento_da_raiz() -> None:
    total = sum(_retained_bytes(service["logging"]) for service in _production_services().values())
    assert total <= MAX_MIB_ALL_SERVICES * MIB


def test_provisionamento_nao_escreve_o_daemon_json_do_host() -> None:
    # O daemon.json vale para os containers de todos os projetos da VPS compartilhada.
    provision = (ROOT / "deploy" / "provision.sh").read_text()
    assert not re.search(r">\s*/etc/docker/daemon\.json", provision)
