"""A publicação legada fica desativada; Coolify é o único ingresso automático."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_SECRETS = {"VPS_SSH_KEY", "VPS_KNOWN_HOSTS"}
SECRET_REFERENCE = re.compile(r"secrets\.([A-Za-z0-9_]+)")


def ci_workflow() -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load(CI_WORKFLOW.read_text())
    return workflow


def deploy_job() -> dict[str, Any]:
    job: dict[str, Any] = ci_workflow()["jobs"]["deploy"]
    return job


def test_deploy_legado_fica_desativado_independentemente_de_variaveis() -> None:
    assert deploy_job()["if"] == "false"


def test_deploy_roda_no_environment_production() -> None:
    assert deploy_job()["environment"] == "production"


def test_deploy_nao_pede_permissao_ao_github_token() -> None:
    """Sem checkout nem chamada à API: o GITHUB_TOKEN não precisa de escopo nenhum."""
    assert deploy_job()["permissions"] == {}


def test_deploy_le_os_segredos_da_vps_por_secrets() -> None:
    referenced = set(SECRET_REFERENCE.findall(yaml.safe_dump(deploy_job())))

    assert referenced == DEPLOY_SECRETS


def test_so_o_job_de_deploy_toca_nos_segredos_da_vps() -> None:
    for name, job in ci_workflow()["jobs"].items():
        if name == "deploy":
            continue
        referenced = set(SECRET_REFERENCE.findall(yaml.safe_dump(job)))
        assert not referenced & DEPLOY_SECRETS, name


def test_jornadas_legadas_dependem_do_deploy_desativado() -> None:
    job = ci_workflow()["jobs"]["vps-smoke"]

    assert job["needs"] == ["deploy"]
    assert "if" not in job
    assert job["uses"] == "./.github/workflows/e2e-vps.yml"
