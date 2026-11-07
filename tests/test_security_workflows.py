"""Auditoria de dependências e CodeQL: fora do ci.yml, sem segredo e sem exceção escondida.

O gate da release (`deploy/coolify/release/gate.py`) exige exatamente os jobs do ci.yml. Estas
análises rodam em workflows próprios para que uma CVE publicada hoje, sem mudança no repositório,
não trave a publicação de uma correção; o ADR 042 registra a escolha.
"""

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
GATE = ROOT / "deploy" / "coolify" / "release" / "gate.py"
AUDIT = WORKFLOWS / "dependency-audit.yml"
CODEQL = WORKFLOWS / "codeql.yml"
PINNED_ACTION = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
# O PyYAML lê a chave `on` como o booleano True.
TRIGGERS = True


def load(path: Path) -> dict[Any, Any]:
    workflow: dict[Any, Any] = yaml.safe_load(path.read_text())
    return workflow


def steps(path: Path) -> list[dict[str, Any]]:
    return [step for job in load(path)["jobs"].values() for step in job["steps"]]


def run_text(path: Path) -> str:
    return "\n".join(step.get("run", "") for step in steps(path))


def gate_module() -> Any:
    spec = importlib.util.spec_from_file_location("release_gate", GATE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_jobs_do_ci_sao_exatamente_os_que_o_gate_da_release_exige() -> None:
    gate = gate_module()
    names = {job["name"] for job in load(WORKFLOWS / "ci.yml")["jobs"].values()}

    assert names == gate.REQUIRED["goblinshift"] | gate.LEGACY_SKIPS


@pytest.mark.parametrize("path", [AUDIT, CODEQL], ids=["pip-audit", "codeql"])
class TestWorkflowDeSeguranca:
    def test_roda_em_pull_request_e_toda_semana(self, path: Path) -> None:
        triggers = load(path)[TRIGGERS]

        assert "pull_request" in triggers
        assert len(triggers["schedule"]) == 1

    def test_actions_fixadas_por_sha_completo(self, path: Path) -> None:
        actions = [step["uses"] for step in steps(path) if "uses" in step]

        assert actions
        assert all(PINNED_ACTION.fullmatch(action) for action in actions), actions

    def test_nao_usa_segredo_nem_guarda_credencial(self, path: Path) -> None:
        assert "secrets." not in path.read_text()
        checkouts = [s for s in steps(path) if s.get("uses", "").startswith("actions/checkout@")]
        assert checkouts
        assert all(step["with"]["persist-credentials"] is False for step in checkouts)

    def test_token_do_workflow_so_le_o_repositorio(self, path: Path) -> None:
        assert load(path)["permissions"] == {"contents": "read"}

    def test_falha_do_scanner_nao_e_ignorada(self, path: Path) -> None:
        assert "continue-on-error" not in path.read_text()


def test_auditoria_le_o_lock_inteiro_com_hashes_e_sem_excecao() -> None:
    commands = run_text(AUDIT)

    assert "uv export --locked --all-groups --no-emit-project" in commands
    assert re.search(r"uvx pip-audit@\d+\.\d+\.\d+ ", commands)
    assert "--require-hashes" in commands
    assert "--disable-pip" in commands
    assert "--ignore-vuln" not in commands


def test_auditoria_nao_pede_permissao_propria() -> None:
    jobs = load(AUDIT)["jobs"].values()

    assert all("permissions" not in job for job in jobs)


def test_codeql_cobre_python_e_typescript_sem_build() -> None:
    job = load(CODEQL)["jobs"]["analyze"]
    uses_init = "github/codeql-action/init@"
    init = next(s for s in job["steps"] if s.get("uses", "").startswith(uses_init))

    assert set(job["strategy"]["matrix"]["language"]) == {"python", "javascript-typescript"}
    assert job["strategy"]["fail-fast"] is False
    assert init["with"]["build-mode"] == "none"


def test_so_o_job_do_codeql_grava_em_security_events() -> None:
    jobs = load(CODEQL)["jobs"]

    assert set(jobs) == {"analyze"}
    assert jobs["analyze"]["permissions"] == {"contents": "read", "security-events": "write"}
