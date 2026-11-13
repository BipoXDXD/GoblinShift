"""Os defeitos plantados do e2e (web/e2e/sabotage.ts) rodam no CI dos PRs da web (issue #426).

O sabotage.ts prova que cada spec cai quando o defeito que ela vigia aparece. Fora do CI, um caso
pode apodrecer sem aviso. Ele leva de 7 a 15 min, o que dobraria o job do Playwright do ci.yml
exigido pelo gate da release; por isso roda num job próprio do e2e-webkit.yml, o workflow de e2e
que já dispara em todo PR que mexe em web/.
"""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "e2e-webkit.yml"
JOB = "sabotagem"
SABOTAGE = "npm run e2e:sabotage"
BUILDS_COPIED = ("npm run build", "npm run build:notice-e2e")
CHROMIUM_INSTALL = "npx playwright install --only-shell chromium"
WEB_PATHS = "web/**"
# O PyYAML lê a chave `on` como o booleano True.
TRIGGERS = True


def load() -> dict[Any, Any]:
    workflow: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    return workflow


def steps() -> list[dict[str, Any]]:
    job_steps: list[dict[str, Any]] = load()["jobs"][JOB]["steps"]
    return job_steps


def step_index(command: str) -> int:
    """Primeiro passo com uma linha igual ao comando (`npm run build` não casa com o do aviso)."""
    return next(
        i
        for i, step in enumerate(steps())
        if command in (line.strip() for line in step.get("run", "").splitlines())
    )


def test_roda_em_todo_pull_request_e_push_que_mexe_na_web() -> None:
    triggers = load()[TRIGGERS]

    assert WEB_PATHS in triggers["pull_request"]["paths"]
    assert WEB_PATHS in triggers["push"]["paths"]


def test_sabotagem_roda_depois_do_navegador_e_dos_dois_builds() -> None:
    sabotage = step_index(SABOTAGE)

    for prerequisite in (CHROMIUM_INSTALL, *BUILDS_COPIED):
        assert step_index(prerequisite) < sabotage


def test_falha_da_sabotagem_reprova_o_job() -> None:
    job = load()["jobs"][JOB]
    step = steps()[step_index(SABOTAGE)]

    assert step["run"].strip() == SABOTAGE
    assert "continue-on-error" not in step
    assert "continue-on-error" not in job
    assert "if" not in step
    assert "if" not in job
