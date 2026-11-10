"""Nenhum passo de apt dos workflows fica preso num espelho lento do Ubuntu.

Pacote do Ubuntu entra só por scripts/ci-apt-instalar.sh (teto por tentativa e troca de espelho);
`apt-get` direto só sobre uma fonte própria (o runsc); o Playwright, que chama o apt por dentro,
roda depois de scripts/ci-apt-sem-azure.sh. Jobs sem segredo guardam os .deb no cache do Actions.
"""

import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(path.name for path in WORKFLOWS_DIR.glob("*.yml"))
INSTALL_SCRIPT = "scripts/ci-apt-instalar.sh"
MIRROR_SCRIPT = "scripts/ci-apt-sem-azure.sh"
PLAYWRIGHT_APT = "playwright install --with-deps"
SINGLE_SOURCE = "Dir::Etc::sourceparts=-"
WORKFLOWS_WITH_UBUNTU_PACKAGES = ("ci.yml", "restore-check.yml", "e2e-producao.yml")
CACHE_RESTORE = "actions/cache/restore@"
CACHE_SAVE = "actions/cache/save@"
PINNED_ACTION = re.compile(r"@[0-9a-f]{40}$")
# Folga para a instalação local depois da última tentativa de download.
INSTALL_MARGIN_S = 60

Step = dict[str, Any]
Job = dict[str, Any]


def _jobs(workflow: str) -> dict[str, Job]:
    document: dict[str, Any] = yaml.safe_load((WORKFLOWS_DIR / workflow).read_text())
    jobs: dict[str, Job] = document["jobs"]
    return jobs


def _all_steps() -> Iterator[tuple[str, Step]]:
    for workflow in WORKFLOWS:
        for job_id, job in _jobs(workflow).items():
            for step in job.get("steps", []):
                yield f"{workflow}:{job_id}:{step.get('name', step.get('uses'))}", step


def _install_jobs() -> list[tuple[str, str]]:
    return [
        (workflow, job_id)
        for workflow in WORKFLOWS
        for job_id, job in _jobs(workflow).items()
        if any(INSTALL_SCRIPT in step.get("run", "") for step in job.get("steps", []))
    ]


def _script_default(name: str) -> int:
    match = re.search(rf"{name}:-(\d+)", (ROOT / INSTALL_SCRIPT).read_text())
    assert match is not None
    return int(match.group(1))


def _mirror_count() -> int:
    match = re.search(r"^espelhos=\((.*)\)$", (ROOT / INSTALL_SCRIPT).read_text(), re.MULTILINE)
    assert match is not None
    return len(match.group(1).split())


def _index(steps: list[Step], predicate: Callable[[Step], bool]) -> int:
    return next(i for i, step in enumerate(steps) if predicate(step))


@pytest.mark.parametrize("workflow", WORKFLOWS_WITH_UBUNTU_PACKAGES)
def test_workflow_instala_pacotes_do_ubuntu_pelo_script(workflow: str) -> None:
    assert any(w == workflow for w, _ in _install_jobs())


def _raw_apt_lines(command: str) -> list[tuple[str, str]]:
    return [
        (where, line)
        for where, step in _all_steps()
        for line in step.get("run", "").splitlines()
        if command in line
    ]


def test_apt_get_update_direto_so_atualiza_fonte_propria() -> None:
    lines = _raw_apt_lines("apt-get update")

    assert lines
    for where, line in lines:
        assert SINGLE_SOURCE in line, where


def test_apt_get_install_direto_so_instala_o_runsc() -> None:
    lines = _raw_apt_lines("apt-get install")

    assert lines
    for where, line in lines:
        assert '"runsc=' in line, where


def test_playwright_com_dependencias_troca_o_espelho_antes() -> None:
    steps = [(w, s) for w, s in _all_steps() if PLAYWRIGHT_APT in s.get("run", "")]

    assert steps
    for where, step in steps:
        assert MIRROR_SCRIPT in step["run"], where
        assert step["run"].index(MIRROR_SCRIPT) < step["run"].index(PLAYWRIGHT_APT), where


def test_passo_do_script_tem_teto_acima_de_todas_as_tentativas() -> None:
    attempts_s = _mirror_count() * _script_default("CI_APT_TENTATIVA_S")
    steps = [(w, s) for w, s in _all_steps() if INSTALL_SCRIPT in s.get("run", "")]

    assert steps
    for where, step in steps:
        assert step["timeout-minutes"] * 60 >= attempts_s + INSTALL_MARGIN_S, where


@pytest.mark.parametrize(("workflow", "job_id"), _install_jobs())
def test_job_com_segredo_nao_usa_cache_e_os_outros_usam(workflow: str, job_id: str) -> None:
    job = _jobs(workflow)[job_id]
    uses = [step.get("uses", "") for step in job["steps"]]
    apt_cache = [u for u in uses if u.startswith((CACHE_RESTORE, CACHE_SAVE))]

    if "environment" in job:
        assert not any(u.startswith("actions/cache") for u in uses)
    else:
        assert len(apt_cache) == 2
        assert all(PINNED_ACTION.search(u) for u in apt_cache)


def _cached_install_jobs() -> list[tuple[str, str]]:
    return [(w, j) for w, j in _install_jobs() if "environment" not in _jobs(w)[j]]


@pytest.mark.parametrize(("workflow", "job_id"), _cached_install_jobs())
def test_cache_restaura_antes_e_salva_depois_com_a_mesma_chave(workflow: str, job_id: str) -> None:
    steps: list[Step] = _jobs(workflow)[job_id]["steps"]
    restore_at = _index(steps, lambda s: s.get("uses", "").startswith(CACHE_RESTORE))
    install_at = _index(steps, lambda s: INSTALL_SCRIPT in s.get("run", ""))
    save_at = _index(steps, lambda s: s.get("uses", "").startswith(CACHE_SAVE))
    restore, install, save = steps[restore_at], steps[install_at], steps[save_at]

    assert restore_at < install_at < save_at
    assert restore["with"] == save["with"]
    assert install["env"]["DEBS"] == restore["with"]["path"]
    assert save["if"] == f"steps.{restore['id']}.outputs.cache-hit != 'true'"


@pytest.mark.parametrize(("workflow", "job_id"), _cached_install_jobs())
def test_chave_do_cache_muda_com_a_imagem_e_com_os_pacotes(workflow: str, job_id: str) -> None:
    steps: list[Step] = _jobs(workflow)[job_id]["steps"]
    restore = steps[_index(steps, lambda s: s.get("uses", "").startswith(CACHE_RESTORE))]
    key_step_id = re.fullmatch(
        r"\$\{\{ steps\.([\w-]+)\.outputs\.valor \}\}", restore["with"]["key"]
    )
    assert key_step_id is not None
    key_step = steps[_index(steps, lambda s: s.get("id") == key_step_id.group(1))]
    install = steps[_index(steps, lambda s: INSTALL_SCRIPT in s.get("run", ""))]

    assert "dpkg-query -W" in key_step["run"]
    assert "$APT_PACOTES" in key_step["run"]
    assert "$APT_PACOTES" in install["run"]


def test_script_de_espelho_e_executavel_e_nao_falha_sem_o_espelho() -> None:
    script = ROOT / MIRROR_SCRIPT

    assert script.stat().st_mode & 0o111
    assert "|| true" in script.read_text()


def test_script_de_instalacao_e_executavel() -> None:
    assert (ROOT / INSTALL_SCRIPT).stat().st_mode & 0o111
