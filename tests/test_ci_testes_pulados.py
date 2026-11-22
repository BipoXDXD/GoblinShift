"""Na CI nenhum teste termina pulado.

O pytest que exige root sai do job comum e roda no passo com sudo; cada execução do Playwright
é seguida da conferência do seu relatório json.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from goblinshift.entrypoints.openapi_export import render_openapi

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
SECURITY_TXT_WORKFLOW = WORKFLOWS / "security-txt.yml"
REAL_CLOCK_SELECTOR = "-m real_clock"
REAL_CLOCK_EXCLUSION = "not real_clock"
ARTE_EXCLUSION = "not arte"
MARKER_SELECTOR = re.compile(r"""-m\s+(?:"([^"]*)"|'([^']*)'|(\S+))""")
NEGATION_PREFIX = "not "
AND_SEPARATOR = " and "
CHECKS_JOB = "checks"
PYTEST_COMMAND = re.compile(r"\bpytest\b")
ROOT_STEP_COMMAND = (
    'sudo -E env "PATH=$PATH" .venv/bin/pytest --fail-on-skip -p no:cacheprovider'
    " -m needs_root tests/executor_ctl"
)
ROOT_ONLY_TEST = "tests/executor_ctl/test_workarea.py"
PLAYWRIGHT_RUN = re.compile(
    r"npm run e2e(:notice|:webkit)?$|npx playwright test -c e2e\b|e2e-gvisor\.sh journeys"
)
SKIP_CHECK = "npm run e2e:check-skips --"
CONTRACT_SCRIPT = ROOT / "scripts" / "contract-test.sh"
CONTRACT_PHASES = "--phases=coverage,fuzzing,stateful"


def _jobs(workflow: Path = CI) -> dict[str, dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load(workflow.read_text())
    jobs: dict[str, dict[str, Any]] = document["jobs"]
    return jobs


def _report_of(step: dict[str, Any]) -> str:
    """O json que a config do Playwright grava para este passo (playwright*.config.ts)."""
    if "webkit" in step["run"]:
        return "e2e-results/webkit.json"
    phase = step.get("env", {}).get("E2E_PHASE", "all")
    return f"e2e-results/{phase}.json"


def _playwright_jobs() -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        (f"{workflow.name}:{job_name}", job.get("steps", []))
        for workflow in sorted(WORKFLOWS.glob("*.yml"))
        for job_name, job in _jobs(workflow).items()
        if any(PLAYWRIGHT_RUN.search(step.get("run", "").strip()) for step in job.get("steps", []))
    ]


def _pytest_commands(workflow: Path = CI) -> list[tuple[str, str]]:
    """Cada `run` do workflow (padrão: a CI) que chama o pytest, com o job em que está."""
    return [
        (job_name, step["run"])
        for job_name, job in _jobs(workflow).items()
        for step in job.get("steps", [])
        if PYTEST_COMMAND.search(step.get("run", ""))
    ]


def test_toda_chamada_do_pytest_na_ci_reprova_teste_pulado() -> None:
    commands = _pytest_commands()

    assert len(commands) >= 4, commands
    assert all("--fail-on-skip" in command for _, command in commands), commands


def test_so_o_passo_com_sudo_do_job_verificacoes_seleciona_os_testes_de_root() -> None:
    root_commands = [
        (job, command) for job, command in _pytest_commands() if "-m needs_root" in command
    ]

    assert root_commands == [(CHECKS_JOB, ROOT_STEP_COMMAND)]


def test_as_demais_chamadas_com_marcador_excluem_os_testes_de_root() -> None:
    # Um `-m` na linha de comando substitui o do `addopts`; cada seletor precisa repetir a exclusão.
    others = [command for _, command in _pytest_commands() if "-m needs_root" not in command]

    assert all("not needs_root" in command for command in others), others


def _only_negations(command: str) -> bool:
    """O `-m` do comando só exclui marcadores (`not a and not b`), sem selecionar nenhum.

    Vale o último `-m`: o primeiro pode ser o `-m pytest` de `python -m pytest`.
    """
    matches = list(MARKER_SELECTOR.finditer(command))
    if not matches:
        return False
    expression = next(group for group in matches[-1].groups() if group is not None)
    return all(term.startswith(NEGATION_PREFIX) for term in expression.split(AND_SEPARATOR))


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ('python -m pytest -m "not x"', True),
        ('.venv/bin/pytest -m "not x and not y"', True),
        ('python -m pytest -m "x and not y"', False),
        ("python -m pytest tests", False),
    ],
)
def test_so_negacoes_le_o_seletor_de_marcadores_e_nao_o_modulo(
    command: str, expected: bool
) -> None:
    assert _only_negations(command) is expected


@pytest.mark.parametrize(
    "exclusion",
    [
        # O lembrete `real_clock` reprovaria PRs sem relação 30 dias antes do vencimento do
        # security.txt.
        pytest.param(REAL_CLOCK_EXCLUSION, id="relogio_real"),
        # Os testes `arte` precisam de Blender e ImageMagick locais e ficam fora da CI.
        pytest.param(ARTE_EXCLUSION, id="arte"),
    ],
)
def test_chamadas_da_ci_que_so_excluem_marcadores_repetem_a_exclusao(exclusion: str) -> None:
    # Um `-m` só de negações substitui o do `addopts` e precisa repetir cada exclusão dele.
    negations = [command for _, command in _pytest_commands() if _only_negations(command)]

    assert negations, _pytest_commands()
    assert all(exclusion in command for command in negations), negations


def test_workflow_do_security_txt_roda_o_lembrete_e_reprova_se_pulado() -> None:
    commands = [command for _, command in _pytest_commands(SECURITY_TXT_WORKFLOW)]

    assert len(commands) == 1, commands
    assert "--fail-on-skip" in commands[0]
    assert REAL_CLOCK_SELECTOR in commands[0]


def test_teste_de_root_fora_de_root_falha_em_vez_de_pular() -> None:
    assert os.geteuid() != 0, "este caso confere o comportamento sem root"

    result = subprocess.run(  # noqa: S603 (só o Python do teste e opções fixas)
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-m",
            "needs_root",
            ROOT_ONLY_TEST,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, result.stdout
    assert "teste needs_root fora de root no Linux" in result.stdout
    assert "skipped" not in result.stdout


def test_cada_execucao_do_playwright_tem_o_relatorio_conferido_depois() -> None:
    jobs = _playwright_jobs()
    assert {name for name, _ in jobs} == {
        "ci.yml:e2e",
        "ci.yml:e2e-gvisor",
        "e2e-vps.yml:vps",
        "e2e-webkit.yml:webkit",
    }

    for name, steps in jobs:
        runs = [
            i for i, step in enumerate(steps) if PLAYWRIGHT_RUN.search(step.get("run", "").strip())
        ]
        checks = [i for i, step in enumerate(steps) if SKIP_CHECK in step.get("run", "")]
        assert len(checks) == 1, name
        check_step = steps[checks[0]]
        assert checks[0] > max(runs), name
        assert "if" not in check_step, name
        checked = check_step["run"].split(SKIP_CHECK, 1)[1].split()
        assert sorted(checked) == sorted(_report_of(steps[i]) for i in runs), name


def _example_paths(node: object, path: str = "") -> list[str]:
    if isinstance(node, dict):
        own = [f"{path}/{key}" for key in node if key in {"example", "examples"}]
        return own + [
            hit for key, value in node.items() for hit in _example_paths(value, f"{path}/{key}")
        ]
    if isinstance(node, list):
        return [
            hit
            for index, value in enumerate(node)
            for hit in _example_paths(value, f"{path}/{index}")
        ]
    return []


def test_schemathesis_declara_as_fases_sem_a_de_examples() -> None:
    assert CONTRACT_PHASES in CONTRACT_SCRIPT.read_text()


def test_nenhuma_operacao_da_spec_tem_example_para_a_fase_omitida() -> None:
    # Se a spec ganhar example(s), a fase `examples` volta a ter o que testar: inclua-a em
    # scripts/contract-test.sh junto com este teste.
    assert _example_paths(json.loads(render_openapi())) == []
