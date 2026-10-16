"""Jornadas na VPS: à mão e depois do deploy, só leitura, Chromium e Firefox, sem a chave SSH.

A chave da conta de teste só pode ir para a produção: nenhuma URL vem de quem dispara o workflow,
e nada que guarde cabeçalhos (traces, test-results) vira artefato.
"""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PRODUCTION = "https://goblinshift.dev"


def load(name: str) -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text())
    return workflow


def vps_job() -> dict[str, Any]:
    job: dict[str, Any] = load("e2e-vps.yml")["jobs"]["vps"]
    return job


def step_named(name: str) -> dict[str, Any]:
    step: dict[str, Any] = next(step for step in vps_job()["steps"] if step.get("name") == name)
    return step


def run_commands() -> str:
    return "\n".join(step.get("run", "") for step in vps_job()["steps"])


def test_roda_a_mao_e_quando_chamado() -> None:
    # O PyYAML lê a chave `on` como o booleano True.
    workflow: dict[Any, Any] = load("e2e-vps.yml")
    triggers = workflow[True]

    assert set(triggers) == {"workflow_dispatch", "workflow_call"}
    assert triggers["workflow_call"]["secrets"]["E2E_VPS_API_KEY"]["required"] is False


def test_nenhuma_url_vem_de_quem_dispara() -> None:
    workflow: dict[Any, Any] = load("e2e-vps.yml")
    triggers = workflow[True]

    for trigger in ("workflow_dispatch", "workflow_call"):
        assert "inputs" not in (triggers[trigger] or {}), trigger
    assert "inputs." not in (WORKFLOWS / "e2e-vps.yml").read_text()
    assert vps_job()["env"]["BASE_URL"] == PRODUCTION
    journeys = step_named("Jornadas de leitura")
    assert journeys["env"]["E2E_BASE_URL"] == PRODUCTION


def test_falha_nao_sobe_traces_nem_test_results() -> None:
    journeys = step_named("Jornadas de leitura")
    assert "--trace off" in journeys["run"]
    uploads = [step for step in vps_job()["steps"] if "upload-artifact" in step.get("uses", "")]
    for upload in uploads:
        assert "test-results" not in upload["with"]["path"]
        assert "trace" not in upload["with"]["path"]


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
