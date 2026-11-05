"""A publicação legada fica desativada; Coolify é o único ingresso automático."""

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
LEGACY_DEPLOY_SCRIPT = ROOT / "deploy" / "deploy.sh"
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


def test_script_de_deploy_legado_recusa_mesmo_pedido_valido_sem_tocar_em_nada(
    tmp_path: Path,
) -> None:
    commit = "a" * 40
    git_log = tmp_path / "git.log"
    fake_git = tmp_path / "git"
    fake_git.write_text(f'#!/bin/sh\necho "$*" >>"{git_log}"\n')
    fake_git.chmod(0o755)

    result = subprocess.run(  # noqa: S603 - argv fixo
        ["bash", str(LEGACY_DEPLOY_SCRIPT), commit, f"sha-{commit[:12]}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )

    assert result.returncode == 64
    assert "Coolify" in result.stderr
    assert not git_log.exists()
