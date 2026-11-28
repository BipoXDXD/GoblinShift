"""Parada e memória compartilhada do contêiner do Postgres (issue #366, containers b e c).

Sem `stop_grace_period`, o Docker manda SIGKILL 10 s depois do SIGINT do fast shutdown: o
checkpoint final é cortado e a subida seguinte faz crash recovery. Sem `shm_size`, o `/dev/shm`
padrão de 64 MiB limita a memória compartilhada dinâmica das queries paralelas.

Os valores valem no Coolify (`deploy/coolify/infra.json`) e no Compose local (`compose.yaml`).
"""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.test_coolify_resource_limits import (
    MIB,
    MIN_HEADROOM,
    PEAK_MEMORY_MIB,
    compose_config,
    limits_of,
    memory_mib,
)

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "deploy" / "coolify" / "infra.json"
COMPOSE = ROOT / "compose.yaml"
POSTGRES_STOP_GRACE = "60s"
POSTGRES_SHM = "128m"
POSTGRES_SHM_MIB = 128


def infra_postgres() -> dict[str, Any]:
    service: dict[str, Any] = json.loads(INFRA.read_text(encoding="utf-8"))["services"]["postgres"]
    return service


def compose_postgres() -> dict[str, Any]:
    service: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"][
        "postgres"
    ]
    return service


@pytest.fixture(params=["infra.json", "compose.yaml"])
def postgres(request: pytest.FixtureRequest) -> dict[str, Any]:
    return infra_postgres() if request.param == "infra.json" else compose_postgres()


def test_postgres_tem_a_carencia_de_parada_decidida(postgres: dict[str, Any]) -> None:
    assert postgres["stop_grace_period"] == POSTGRES_STOP_GRACE


def test_postgres_tem_o_shm_decidido(postgres: dict[str, Any]) -> None:
    assert postgres["shm_size"] == POSTGRES_SHM


def test_shm_e_o_pico_cabem_no_teto_de_memoria() -> None:
    # /dev/shm é tmpfs e conta no cgroup de memória do contêiner.
    shm_mib = int(infra_postgres()["shm_size"].removesuffix("m"))
    peak = MIN_HEADROOM * PEAK_MEMORY_MIB["postgres"]

    assert shm_mib + peak <= memory_mib(limits_of(infra_postgres()))


def test_o_compose_interpreta_o_shm_do_postgres() -> None:
    assert int(compose_config(INFRA)["services"]["postgres"]["shm_size"]) == POSTGRES_SHM_MIB * MIB
