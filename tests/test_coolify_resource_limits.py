"""Tetos de memória, CPU e processos dos serviços publicados pelo Coolify (ADR 011).

A VPS KVM 2 (2 vCPU, 7,9 GiB) é compartilhada com outros projetos. Sem teto, um vazamento ou um
pico de um container do GoblinShift tira memória dos vizinhos e o OOM killer do host escolhe a
vítima. Com `deploy.resources.limits`, o pior caso fica dentro do próprio container.

Os arquivos conferidos são os que o Coolify lê em produção (`deploy/coolify/app.json` e
`deploy/coolify/infra.json`, Compose em JSON). Além das chaves, o `docker compose config` confirma
que o Compose entende os valores do jeito pretendido.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
COOLIFY_DIR = ROOT / "deploy" / "coolify"
COOLIFY_FILES = (COOLIFY_DIR / "app.json", COOLIFY_DIR / "infra.json")
REQUIRED_LIMITS = frozenset({"memory", "cpus", "pids"})
MEMORY = re.compile(r"^(\d+)M$")
MIB = 1024 * 1024
# Nenhum serviço pode ocupar mais da metade das 2 vCPUs do host compartilhado.
MAX_CPUS_PER_SERVICE = 1.0
# Soma dos tetos dos serviços contínuos: menos de um terço dos 7,9 GiB, que dividimos com outros
# 4 apps. Migração e storage-init rodam uma vez antes da API e não somam com o pico dela.
MAX_TOTAL_MEMORY_MIB = 2560
ONE_SHOT_RESTART = "no"
MIN_PIDS = 32
MAX_PIDS = 512
# Pico de RAM medido em produção em 2026-10-28 (docker stats, MiB). O teto precisa de folga sobre
# ele para que um pico normal não vire OOM kill.
PEAK_MEMORY_MIB = {
    "api": 192,
    "worker": 176,
    "maintenance": 175,
    "nginx": 17,
    "otel-collector": 150,
    "postgres": 64,
}
MIN_HEADROOM = 1.5
OPENOBSERVE_MEMORY_SETTINGS = (
    "ZO_MEMORY_CACHE_MAX_SIZE",
    "ZO_MEMORY_CACHE_DATAFUSION_MAX_SIZE",
    "ZO_MEM_TABLE_MAX_SIZE",
)
CONFIG_TIMEOUT_SECONDS = 60
REQUIRED_VARIABLE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*):\?")


def load(path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def all_services() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path.name, name, service)
        for path in COOLIFY_FILES
        for name, service in load(path)["services"].items()
    ]


def limits_of(service: dict[str, Any]) -> dict[str, Any]:
    limits: dict[str, Any] = service.get("deploy", {}).get("resources", {}).get("limits", {})
    return limits


def memory_mib(limits: dict[str, Any]) -> int:
    match = MEMORY.fullmatch(str(limits["memory"]))
    assert match is not None, f"memória em MiB com sufixo M: {limits['memory']!r}"
    return int(match.group(1))


def service_ids(entry: tuple[str, str, dict[str, Any]]) -> str:
    return f"{entry[0]}:{entry[1]}"


SERVICES = all_services()


@pytest.mark.parametrize("entry", SERVICES, ids=[service_ids(entry) for entry in SERVICES])
def test_todo_servico_tem_teto_de_memoria_cpu_e_processos(
    entry: tuple[str, str, dict[str, Any]],
) -> None:
    limits = limits_of(entry[2])

    assert set(limits) == REQUIRED_LIMITS


@pytest.mark.parametrize("entry", SERVICES, ids=[service_ids(entry) for entry in SERVICES])
def test_os_tetos_tem_unidades_e_faixas_validas(entry: tuple[str, str, dict[str, Any]]) -> None:
    limits = limits_of(entry[2])

    assert memory_mib(limits) > 0
    assert isinstance(limits["cpus"], str)
    assert 0 < float(limits["cpus"]) <= MAX_CPUS_PER_SERVICE
    assert isinstance(limits["pids"], int)
    assert MIN_PIDS <= limits["pids"] <= MAX_PIDS


@pytest.mark.parametrize("name", sorted(PEAK_MEMORY_MIB))
def test_o_teto_de_memoria_tem_folga_sobre_o_pico_medido(name: str) -> None:
    service = next(service for _, other, service in SERVICES if other == name)

    assert memory_mib(limits_of(service)) >= MIN_HEADROOM * PEAK_MEMORY_MIB[name]


def test_a_soma_dos_tetos_dos_servicos_continuos_cabe_no_orcamento_do_host() -> None:
    total = sum(
        memory_mib(limits_of(service))
        for _, _, service in SERVICES
        if service["restart"] != ONE_SHOT_RESTART
    )

    assert total <= MAX_TOTAL_MEMORY_MIB


def openobserve() -> dict[str, Any]:
    service: dict[str, Any] = load(COOLIFY_DIR / "infra.json")["services"]["openobserve"]
    return service


def test_o_openobserve_fixa_os_caches_em_memoria_abaixo_do_teto_do_container() -> None:
    service = openobserve()
    environment = service["environment"]

    caches_mib = sum(int(environment[setting]) for setting in OPENOBSERVE_MEMORY_SETTINGS)

    # A outra metade fica para o runtime, a base GeoIP carregada em memória e o page cache.
    assert caches_mib <= memory_mib(limits_of(service)) / 2


def test_o_openobserve_mantem_a_retencao_de_30_dias_da_janela_dos_slos() -> None:
    assert openobserve()["environment"]["ZO_COMPACT_DATA_RETENTION_DAYS"] == "30"


def placeholder_environment(path: Path) -> dict[str, str]:
    required = set(REQUIRED_VARIABLE.findall(path.read_text(encoding="utf-8")))
    return {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", "/"),
        **dict.fromkeys(required, "placeholder"),
    }


def compose_config(path: Path) -> dict[str, Any]:
    docker = shutil.which("docker")
    assert docker is not None, "o teste dos tetos precisa do docker (com o plugin compose)"
    result = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [
            *(docker, "compose", "--project-directory", str(ROOT), "-f", str(path)),
            *("config", "--format", "json", "--no-path-resolution"),
        ],
        capture_output=True,
        text=True,
        env=placeholder_environment(path),
        check=False,
        timeout=CONFIG_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, result.stderr
    config: dict[str, Any] = json.loads(result.stdout)
    return config


@pytest.mark.parametrize("path", COOLIFY_FILES, ids=[path.name for path in COOLIFY_FILES])
def test_o_compose_interpreta_os_tetos_como_declarados(path: Path) -> None:
    declared = load(path)["services"]

    resolved = compose_config(path)["services"]

    for name, service in declared.items():
        limits = limits_of(service)
        applied = resolved[name]["deploy"]["resources"]["limits"]
        assert int(applied["memory"]) == memory_mib(limits) * MIB, name
        assert float(applied["cpus"]) == float(limits["cpus"]), name
        assert int(applied["pids"]) == limits["pids"], name
