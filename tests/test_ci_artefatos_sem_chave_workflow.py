"""Todo upload de relatório do Playwright passa antes pelo canário de chave de API.

O job do gVisor subiu trace.zip com a chave da conta de teste (repositório público, 7 dias). O
canário (scripts/verificar_artefatos_sem_chave.py) roda com `if: always()` antes do upload, e o
upload só roda se ele passou, além da condição que o upload já tinha.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CANARY_SCRIPT = "verificar_artefatos_sem_chave.py"
PLAYWRIGHT_DIRECTORY = re.compile(r"(playwright-report|test-results)[\w-]*/$")
UPLOAD_ACTION = "actions/upload-artifact@"
REPORT_PATH_MARKERS = ("playwright-report", "test-results")
# Upload de test-results/playwright-report sem o gate do canário, aceito de propósito. Cada entrada
# é (workflow, job, nome do artefato) e precisa do motivo ao lado.
UNGATED_UPLOADS = {
    # Só os controles-inclinacao.png (capturas de tela para aprovação visual): PNG não guarda
    # cabeçalhos de pedido, que era o vetor da chave no trace.zip. Roda com `if: always()` antes do
    # canário do job de propósito, para a captura subir mesmo se o canário falhar mais adiante.
    ("ci.yml", "e2e", "controles-inclinacao-3d"),
}


def playwright_uploads() -> list[tuple[str, str, int, list[dict[str, Any]]]]:
    """(workflow, job, índice do passo, passos do job) de cada upload de diretório do Playwright."""
    found = []
    for workflow_path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(workflow_path.read_text())
        for job_name, job in workflow.get("jobs", {}).items():
            steps = job.get("steps", [])
            for index, step in enumerate(steps):
                if not step.get("uses", "").startswith(UPLOAD_ACTION):
                    continue
                lines = str(step["with"]["path"]).split()
                if any(PLAYWRIGHT_DIRECTORY.search(line) for line in lines):
                    found.append((workflow_path.name, job_name, index, steps))
    return found


UPLOADS = playwright_uploads()
IDS = [f"{workflow}:{job}:{index}" for workflow, job, index, _ in UPLOADS]


def test_encontra_os_uploads_do_playwright_de_cada_workflow() -> None:
    assert {(workflow, job) for workflow, job, _, _ in UPLOADS} == {
        ("ci.yml", "e2e"),
        ("ci.yml", "e2e-gvisor"),
        ("e2e-webkit.yml", "webkit"),
        ("e2e-vps.yml", "vps"),
    }


@pytest.mark.parametrize(("workflow", "job", "index", "steps"), UPLOADS, ids=IDS)
def test_upload_so_roda_se_o_canario_passou(
    workflow: str, job: str, index: int, steps: list[dict[str, Any]]
) -> None:
    upload = steps[index]
    canaries = [
        step
        for step in steps[:index]
        if CANARY_SCRIPT in step.get("run", "") and step.get("id") is not None
    ]
    assert canaries, f"{workflow}:{job} não tem o passo do canário com id antes do upload"
    canary = canaries[-1]
    assert canary["if"] == "always()"
    assert f"steps.{canary['id']}.outcome == 'success'" in upload["if"]


@pytest.mark.parametrize(("workflow", "job", "index", "steps"), UPLOADS, ids=IDS)
def test_canario_varre_os_diretorios_do_upload(
    workflow: str, job: str, index: int, steps: list[dict[str, Any]]
) -> None:
    upload = steps[index]
    canary = next(step for step in reversed(steps[:index]) if CANARY_SCRIPT in step.get("run", ""))
    for line in str(upload["with"]["path"]).split():
        if PLAYWRIGHT_DIRECTORY.search(line):
            diretorio = line.rstrip("/").rsplit("/", 1)[-1]
            assert diretorio in canary["run"], f"{diretorio} fora do canário em {workflow}:{job}"


def report_uploads_without_gate(
    workflow_name: str, workflow: dict[str, Any], gated: set[tuple[str, str, int]]
) -> set[tuple[str, str, str]]:
    """Uploads cujo path cita test-results ou playwright-report e que não passam pelo canário."""
    found = set()
    for job_name, job in workflow.get("jobs", {}).items():
        for index, step in enumerate(job.get("steps", [])):
            if not step.get("uses", "").startswith(UPLOAD_ACTION):
                continue
            path = str(step["with"]["path"])
            if not any(marker in path for marker in REPORT_PATH_MARKERS):
                continue
            if (workflow_name, job_name, index) not in gated:
                found.add((workflow_name, job_name, str(step["with"]["name"])))
    return found


def test_upload_de_relatorio_sem_canario_so_o_da_lista_de_excecoes() -> None:
    gated = {(workflow, job, index) for workflow, job, index, _ in UPLOADS}
    found: set[tuple[str, str, str]] = set()
    for workflow_path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = yaml.safe_load(workflow_path.read_text())
        found |= report_uploads_without_gate(workflow_path.name, workflow, gated)

    assert found == UNGATED_UPLOADS


def test_outro_upload_de_test_results_sem_canario_e_apontado() -> None:
    workflow = yaml.safe_load(
        """
jobs:
  e2e:
    steps:
      - uses: actions/upload-artifact@v7
        with: {name: capturas-novas, path: web/test-results/**/outra.png}
      - uses: actions/upload-artifact@v7
        with: {name: relatorio-novo, path: "web/playwright-report-x/"}
      - uses: actions/upload-artifact@v7
        with: {name: sem-relacao, path: dist/}
"""
    )

    found = report_uploads_without_gate("novo.yml", workflow, gated=set())

    assert found == {("novo.yml", "e2e", "capturas-novas"), ("novo.yml", "e2e", "relatorio-novo")}
