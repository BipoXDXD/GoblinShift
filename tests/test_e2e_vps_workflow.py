"""Jornadas na VPS: à mão e depois do deploy, só leitura, Chromium e Firefox, sem a chave SSH."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load(name: str) -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text())
    return workflow


def vps_job() -> dict[str, Any]:
    job: dict[str, Any] = load("e2e-vps.yml")["jobs"]["vps"]
    return job


def run_commands() -> str:
    return "\n".join(step.get("run", "") for step in vps_job()["steps"])


def test_roda_a_mao_e_quando_chamado() -> None:
    # O PyYAML lê a chave `on` como o booleano True.
    workflow: dict[Any, Any] = load("e2e-vps.yml")
    triggers = workflow[True]

    assert set(triggers) == {"workflow_dispatch", "workflow_call"}
    assert triggers["workflow_call"]["secrets"]["E2E_VPS_API_KEY"]["required"] is False


def test_le_o_secret_opcional_no_environment_production() -> None:
    assert vps_job()["environment"] == "production"


def test_confere_a_borda_e_roda_as_jornadas_nos_dois_navegadores() -> None:
    commands = run_commands()

    assert 'scripts/check-spa-edge.sh "$BASE_URL"' in commands
    assert "playwright install --with-deps chromium firefox" in commands
    journeys = next(
        step for step in vps_job()["steps"] if step.get("name") == "Jornadas de leitura"
    )
    assert journeys["env"]["E2E_VPS"] == "1"


def test_so_le_o_conteudo_do_repositorio() -> None:
    assert load("e2e-vps.yml")["permissions"] == {"contents": "read"}


def test_o_ci_chama_depois_do_deploy_sem_herdar_os_segredos() -> None:
    job = load("ci.yml")["jobs"]["vps-smoke"]

    assert job["needs"] == ["deploy"]
    assert job["uses"] == "./.github/workflows/e2e-vps.yml"
    assert job["secrets"] == {"E2E_VPS_API_KEY": "${{ secrets.E2E_VPS_API_KEY }}"}
