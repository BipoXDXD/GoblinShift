"""Job no environment production: só roda na main e não restaura cache.

Um push numa branch grava cache no escopo dela; um job com a chave de produção disparado dessa
branch restauraria o `node_modules` envenenado antes de rodar com o secret no env.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TRAVA_DA_MAIN = "github.ref == 'refs/heads/main'"

# Cache que a action liga sozinha, sem `cache:` no passo: em job de produção, desligado explícito.
CACHE_OFF: dict[str, tuple[str, object]] = {
    "astral-sh/setup-uv": ("enable-cache", False),
    "actions/setup-go": ("cache", False),
    "actions/setup-node": ("package-manager-cache", False),
}


def _jobs_of(arquivo: str) -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load((WORKFLOWS / arquivo).read_text())
    jobs: dict[str, Any] = workflow["jobs"]
    return jobs


def _e_production(environment: object) -> bool:
    nome = environment.get("name") if isinstance(environment, dict) else environment
    # Nome calculado escaparia da checagem: o job poderia cair em production sem a trava da main.
    assert "${{" not in str(nome), f"environment com expressão não é auditável: {nome!r}"
    return nome == "production"


def _production_jobs() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (caminho.name, job_id, job)
        for caminho in sorted(WORKFLOWS.glob("*.yml"))
        for job_id, job in _jobs_of(caminho.name).items()
        if _e_production(job.get("environment"))
    ]


def _normalizado(expr: str) -> str:
    expr = expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2]
    return " ".join(expr.split())


def _tem_ou_no_nivel_de_cima(expr: str) -> bool:
    profundidade = 0
    em_string = False
    for i, caractere in enumerate(expr):
        if caractere == "'":
            em_string = not em_string
        elif em_string:
            continue
        elif caractere == "(":
            profundidade += 1
        elif caractere == ")":
            profundidade -= 1
        elif profundidade == 0 and expr.startswith("||", i):
            return True
    return False


def _ignora_needs_pulado(expr: str) -> bool:
    # `!cancelled()` contém `cancelled()`; `success() || x` também roda com o need pulado.
    return re.search(r"\b(always|cancelled|failure|success)\(\)", expr) is not None


def _travado_na_main(jobs: dict[str, Any], job_id: str) -> bool:
    """O `if` do job exige a main, ou herda a trava de um job em `needs` que ele não ignora."""
    expr = _normalizado(str(jobs[job_id].get("if", "")))
    prefixo = f"{TRAVA_DA_MAIN} && "
    if expr == TRAVA_DA_MAIN or (
        expr.startswith(prefixo) and not _tem_ou_no_nivel_de_cima(expr[len(prefixo) :])
    ):
        return True
    if _ignora_needs_pulado(expr):
        return False
    needs = jobs[job_id].get("needs", [])
    if isinstance(needs, str):
        needs = [needs]
    return any(_travado_na_main(jobs, dependencia) for dependencia in needs)


def test_job_no_environment_production_so_roda_na_main() -> None:
    jobs = _production_jobs()
    assert len(jobs) >= 3  # e2e-vps/vps, e2e-producao/journey, coolify/deploy
    assert [(a, j) for a, j, _ in jobs if not _travado_na_main(_jobs_of(a), j)] == []


def test_job_no_environment_production_nao_restaura_cache() -> None:
    for arquivo, job_id, job in _production_jobs():
        for passo in job.get("steps", []):
            action = passo.get("uses", "").split("@")[0]
            assert not action.startswith("actions/cache"), (arquivo, job_id)
            if action == "actions/setup-node":
                assert "cache" not in (passo.get("with") or {}), (arquivo, job_id)
            if action in CACHE_OFF:
                chave, valor = CACHE_OFF[action]
                assert (passo.get("with") or {}).get(chave) is valor, (arquivo, job_id, action)


@pytest.mark.parametrize(
    "expr",
    [
        "github.ref == 'refs/heads/main' && a || b",
        "a || github.ref == 'refs/heads/main'",
        "github.ref != 'refs/heads/main'",
    ],
)
def test_trava_da_main_recusa_ou_no_nivel_de_cima(expr: str) -> None:
    assert not _travado_na_main({"j": {"if": expr}}, "j")


@pytest.mark.parametrize(
    "status", ["always()", "cancelled()", "!cancelled()", "failure()", "success()"]
)
def test_funcao_de_status_com_ou_nao_herda_trava_do_needs(status: str) -> None:
    jobs = {
        "pai": {"if": TRAVA_DA_MAIN},
        "j": {"if": f"{status} || github.event_name == 'x'", "needs": ["pai"]},
    }
    assert not _travado_na_main(jobs, "j")


@pytest.mark.parametrize(
    "environment",
    ["${{ inputs.environment }}", {"name": "${{ inputs.environment }}"}],
)
def test_environment_com_expressao_reprova(environment: object) -> None:
    with pytest.raises(AssertionError, match="expressão"):
        _e_production(environment)
