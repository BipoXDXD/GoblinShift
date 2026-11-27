"""A publicação legada fica desativada; Coolify é o único ingresso automático."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
LEGACY_DEPLOY_SCRIPT = ROOT / "deploy" / "deploy.sh"


def ci_workflow() -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load(CI_WORKFLOW.read_text())
    return workflow


def deploy_job() -> dict[str, Any]:
    job: dict[str, Any] = ci_workflow()["jobs"]["deploy"]
    return job


def test_deploy_legado_prova_recusa_e_exercita_o_receptor_nos_dois_eventos() -> None:
    job = deploy_job()
    assert "if" not in job
    assert "environment" not in job
    run = "\n".join(step.get("run", "") for step in job["steps"])
    assert "deploy/deploy.sh" in run
    assert 'test "$rc" -eq 64' in run
    assert "python3 -m unittest discover -s deploy/coolify/release -v" in run
    assert "python3 deploy/coolify/release/assets.py" in run


def test_ci_nao_tem_acesso_a_publicacao_e_a_segredos_da_vps() -> None:
    ci = ci_workflow()
    assert ci["permissions"] == {"contents": "read"}
    for name, job in ci["jobs"].items():
        assert "environment" not in job, name
        assert "secrets" not in job, name
        assert "secrets." not in yaml.safe_dump(job), name
        # Só o token OIDC e o atestado dos jobs de imagem escrevem (tests/test_ci_atestado.py);
        # nada de escrita em registro, conteúdo ou qualquer outro escopo.
        escrita = {k for k, v in job.get("permissions", {}).items() if v == "write"}
        assert escrita <= {"id-token", "attestations"}, name


def test_as_jornadas_locais_rodam_apos_o_contrato_do_receptor() -> None:
    job = ci_workflow()["jobs"]["vps-smoke"]
    assert "deploy" in job["needs"]
    assert "if" not in job
    assert "uses" not in job
    assert any(step.get("run") == "scripts/e2e-ci-release.sh" for step in job["steps"])


COMMIT = "a" * 40
VALID_REQUEST = (COMMIT, f"sha-{COMMIT[:12]}")


@pytest.mark.parametrize(
    ("args", "ssh_command"),
    [
        (VALID_REQUEST, ""),
        ((), f"deploy {' '.join(VALID_REQUEST)}"),
    ],
    ids=["argumentos", "forced-command"],
)
def test_script_de_deploy_legado_recusa_mesmo_pedido_valido_sem_tocar_em_nada(
    tmp_path: Path, args: tuple[str, ...], ssh_command: str
) -> None:
    git_log = tmp_path / "git.log"
    fake_git = tmp_path / "git"
    fake_git.write_text(f'#!/bin/sh\necho "$*" >>"{git_log}"\n')
    fake_git.chmod(0o755)

    result = subprocess.run(  # noqa: S603 - argv fixo
        ["bash", str(LEGACY_DEPLOY_SCRIPT), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "SSH_ORIGINAL_COMMAND": ssh_command,
        },
    )

    assert result.returncode == 64
    assert "Coolify" in result.stderr
    assert not git_log.exists()
