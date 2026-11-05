"""Conferência da conta de teste antes da jornada com escrita (scripts/e2e_quota_check.py).

Os casos de cota ficam nas duas pontas da fronteira: a rodada que cabe exatamente e a que passa
por uma unidade. Um pedido a mais na janela de 24 h responderia 429 no meio da jornada.
"""

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from goblinshift.files.limits import ARTIFACT_TTL, READY_UPLOAD_TTL, USED_INPUT_TTL
from goblinshift.identity.principal import Scope
from goblinshift.runs.quotas import MAX_RUNS_PER_WINDOW, QUOTA_WINDOW
from scripts import e2e_fixtures, e2e_quota_check

CHECKER = Path(e2e_quota_check.__file__)
GIB = 1024**3
RUNS_PER_DAY = 30
INPUT_PER_DAY = 2 * GIB
STORAGE = 4 * GIB
RETENTION_HOURS = 48
HOUR_SECONDS = 3600
# Cada script roda o mesmo pedido duas vezes para conferir o determinismo.
ROUNDS_PER_SCRIPT = 2
PRODUCTION_RECIPES = ("pdf-press", "media-mill")

ACCOUNT: dict[str, Any] = {
    "scopes": ["files:read", "files:write", "runs:read", "runs:write"],
    "limits": {
        "runs_per_24h": RUNS_PER_DAY,
        "input_bytes_per_24h": INPUT_PER_DAY,
        "storage_bytes": STORAGE,
    },
    "usage": {
        "runs_last_24h": 0,
        "input_bytes_last_24h": 0,
        "storage_reserved_bytes": 0,
        "active_run_id": None,
    },
    "terms": {"acceptance_required": False},
}


def account(**usage: object) -> dict[str, Any]:
    changed = copy.deepcopy(ACCOUNT)
    changed["usage"].update(usage)
    return changed


def run_checker(me: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - script do repositório
        [sys.executable, "-I", str(CHECKER)],
        input=json.dumps(me),
        capture_output=True,
        text=True,
        check=False,
    )


def test_conta_folgada_comporta_a_rodada() -> None:
    assert e2e_quota_check.shortfall(ACCOUNT) is None


@pytest.mark.parametrize(
    ("usage", "fits"),
    [
        ({"runs_last_24h": RUNS_PER_DAY - e2e_quota_check.JOURNEY_RUNS}, True),
        ({"runs_last_24h": RUNS_PER_DAY - e2e_quota_check.JOURNEY_RUNS + 1}, False),
        ({"runs_last_24h": RUNS_PER_DAY}, False),
        ({"input_bytes_last_24h": INPUT_PER_DAY - e2e_quota_check.JOURNEY_BYTES}, True),
        ({"input_bytes_last_24h": INPUT_PER_DAY - e2e_quota_check.JOURNEY_BYTES + 1}, False),
        ({"storage_reserved_bytes": STORAGE - e2e_quota_check.JOURNEY_BYTES}, True),
        ({"storage_reserved_bytes": STORAGE - e2e_quota_check.JOURNEY_BYTES + 1}, False),
    ],
)
def test_fronteiras_da_cota(usage: dict[str, int], fits: bool) -> None:
    assert (e2e_quota_check.shortfall(account(**usage)) is None) is fits


def test_execucao_ativa_ocupa_a_vaga_unica_da_conta() -> None:
    busy = account(active_run_id="0198f0a0-0000-7000-8000-000000000000")

    reason = e2e_quota_check.shortfall(busy)

    assert reason is not None
    assert "ativa" in reason


def test_cli_com_folga_imprime_a_saida_do_passo() -> None:
    result = run_checker(ACCOUNT)

    assert result.returncode == 0
    assert result.stdout == "fits=true\n"


def test_cli_sem_folga_pula_a_rodada_sem_falhar() -> None:
    result = run_checker(account(runs_last_24h=RUNS_PER_DAY))

    assert result.returncode == 0
    assert result.stdout == "fits=false\n"
    assert "::notice" in result.stderr


def test_cli_sem_o_aceite_dos_termos_falha() -> None:
    me = copy.deepcopy(ACCOUNT)
    me["terms"]["acceptance_required"] = True

    result = run_checker(me)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "termos" in result.stderr


@pytest.mark.parametrize("scope", sorted(e2e_quota_check.REQUIRED_SCOPES))
def test_cli_sem_um_escopo_falha_citando_qual(scope: str) -> None:
    me = copy.deepcopy(ACCOUNT)
    me["scopes"].remove(scope)

    result = run_checker(me)

    assert result.returncode == 1
    assert result.stdout == ""
    assert scope in result.stderr


def test_os_escopos_exigidos_existem_na_api() -> None:
    assert {scope.value for scope in Scope} >= e2e_quota_check.REQUIRED_SCOPES


def test_rodada_conta_as_execucoes_dos_dois_scripts() -> None:
    assert ROUNDS_PER_SCRIPT * len(PRODUCTION_RECIPES) == e2e_quota_check.JOURNEY_RUNS
    assert e2e_quota_check.JOURNEY_RUNS < MAX_RUNS_PER_WINDOW


def test_as_fixtures_cabem_no_teto_de_bytes_da_rodada() -> None:
    # A mesma entrada em duas execuções conta duas vezes na cota de entrada processada.
    inputs = sum(
        len(fixture.content)
        for recipe in PRODUCTION_RECIPES
        for fixture in e2e_fixtures.build(recipe).fixtures
    )

    assert ROUNDS_PER_SCRIPT * inputs <= e2e_quota_check.JOURNEY_BYTES


def test_dados_de_teste_expiram_em_48_horas_sem_acumular_armazenamento() -> None:
    longest = max(ARTIFACT_TTL, USED_INPUT_TTL, READY_UPLOAD_TTL)
    rounds_per_day = MAX_RUNS_PER_WINDOW // e2e_quota_check.JOURNEY_RUNS

    assert longest.total_seconds() == RETENTION_HOURS * HOUR_SECONDS
    assert QUOTA_WINDOW.total_seconds() == 24 * HOUR_SECONDS
    assert 2 * rounds_per_day * e2e_quota_check.JOURNEY_BYTES < STORAGE
