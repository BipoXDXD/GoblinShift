"""Todo passo da CI que usa o apt passa pelo script que tira o espelho do Azure."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/ci-apt-sem-azure.sh"
WORKFLOWS = ["ci.yml", "restore-check.yml", "e2e-vps.yml"]
# O `--with-deps` do Playwright chama o apt por dentro.
APT_COMMANDS = ("apt-get update", "playwright install --with-deps")


def _apt_command(run: str) -> str | None:
    return next((command for command in APT_COMMANDS if command in run), None)


def _apt_steps(workflow: str) -> list[dict[str, str]]:
    document = yaml.safe_load((ROOT / ".github" / "workflows" / workflow).read_text())
    return [
        step
        for job in document["jobs"].values()
        for step in job.get("steps", [])
        if _apt_command(step.get("run", ""))
    ]


@pytest.mark.parametrize("workflow", WORKFLOWS)
def test_passos_de_apt_trocam_o_espelho_antes_do_update(workflow: str) -> None:
    steps = _apt_steps(workflow)

    assert steps
    for step in steps:
        command = _apt_command(step["run"])
        assert command is not None
        assert SCRIPT in step["run"]
        assert step["run"].index(SCRIPT) < step["run"].index(command)


def test_script_e_executavel_e_nao_falha_sem_o_espelho() -> None:
    script = ROOT / SCRIPT

    assert script.stat().st_mode & 0o111
    assert "|| true" in script.read_text()
