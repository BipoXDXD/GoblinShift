"""Todo passo da CI que usa o apt passa pelo script que tira o espelho do Azure."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/ci-apt-sem-azure.sh"
WORKFLOWS = ["ci.yml", "restore-check.yml"]


def _apt_steps(workflow: str) -> list[dict[str, str]]:
    document = yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text())
    return [
        step
        for job in document["jobs"].values()
        for step in job.get("steps", [])
        if "apt-get update" in step.get("run", "")
    ]


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_passos_de_apt_trocam_o_espelho_antes_do_update(workflow: str) -> None:
    steps = _apt_steps(workflow)

    assert steps
    for step in steps:
        assert step["run"].index(SCRIPT) < step["run"].index("apt-get update")


def test_script_e_executavel_e_nao_falha_sem_o_espelho() -> None:
    script = ROOT / SCRIPT

    assert script.stat().st_mode & 0o111
    assert "|| true" in script.read_text()
