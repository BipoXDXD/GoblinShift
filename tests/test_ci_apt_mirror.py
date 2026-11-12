"""Nenhum passo de apt dos workflows fica preso num espelho lento do Ubuntu.

Pacote do Ubuntu entra só por scripts/ci-apt-instalar.sh (teto por tentativa e troca de espelho);
`apt-get` direto só sobre uma fonte própria (o runsc, em scripts/ci-install-runsc.sh, que os jobs
com gVisor chamam). O Playwright nunca chama o apt: as dependências dele vêm do mesmo script
(scripts/ci-playwright-pacotes.sh lista quais) e o navegador entra sem `--with-deps`. Jobs sem
segredo e com lista fixa de pacotes guardam os .deb no cache.
"""

import os
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(path.name for path in WORKFLOWS_DIR.glob("*.yml"))
INSTALL_SCRIPT = "scripts/ci-apt-instalar.sh"
RUNSC_SCRIPT = "scripts/ci-install-runsc.sh"
MIRROR_SCRIPT = "scripts/ci-apt-sem-azure.sh"
PLAYWRIGHT_PACKAGES_SCRIPT = "scripts/ci-playwright-pacotes.sh"
PLAYWRIGHT_INSTALL = "npx playwright install "
PLAYWRIGHT_APT = ("--with-deps", "install-deps")
PLAYWRIGHT_WORKFLOWS = ("ci.yml", "e2e-vps.yml")
SINGLE_SOURCE = "Dir::Etc::sourceparts=-"
WORKFLOWS_WITH_UBUNTU_PACKAGES = ("ci.yml", "restore-check.yml", "e2e-producao.yml")
CACHE_RESTORE = "actions/cache/restore@"
CACHE_SAVE = "actions/cache/save@"
PINNED_ACTION = re.compile(r"@[0-9a-f]{40}$")
# Folga para a instalação local depois da última tentativa de download.
INSTALL_MARGIN_S = 60
STEP_TIMEOUT_S = 10
# Como o GitHub roda `run:` com `shell: bash`.
GITHUB_BASH = ("bash", "--noprofile", "--norc", "-eo", "pipefail", "-c")
FAKE_SCRIPT = """#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$FAKE_LOG"
printf '%s' "${FAKE_LISTA:-}"
"""

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
    # Os passos dos workflows e o instalador do runsc, onde o apt direto foi parar.
    in_workflows = [
        (where, line)
        for where, step in _all_steps()
        for line in step.get("run", "").splitlines()
        if command in line
    ]
    in_runsc_script = [
        (RUNSC_SCRIPT, line)
        for line in (ROOT / RUNSC_SCRIPT).read_text().splitlines()
        if command in line
    ]
    return in_workflows + in_runsc_script


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


def _playwright_steps() -> list[tuple[str, Step]]:
    """Passos que instalam navegador num runner Ubuntu (o do macOS não tem apt)."""
    return [
        (f"{workflow}:{job_id}", step)
        for workflow in WORKFLOWS
        for job_id, job in _jobs(workflow).items()
        if str(job.get("runs-on", "")).startswith("ubuntu")
        for step in job.get("steps", [])
        if PLAYWRIGHT_INSTALL in step.get("run", "")
    ]


@pytest.mark.parametrize("workflow", PLAYWRIGHT_WORKFLOWS)
def test_workflow_instala_navegador_do_playwright(workflow: str) -> None:
    assert any(where.startswith(f"{workflow}:") for where, _ in _playwright_steps())


def test_playwright_nunca_chama_o_apt() -> None:
    for where, step in _all_steps():
        run = step.get("run", "")
        if "playwright" in run:
            assert not any(flag in run for flag in PLAYWRIGHT_APT), where


def test_dependencias_do_playwright_vem_do_script_antes_do_navegador() -> None:
    for where, step in _playwright_steps():
        run = step["run"]
        assert run.index(PLAYWRIGHT_PACKAGES_SCRIPT) < run.index(INSTALL_SCRIPT), where
        assert run.index(INSTALL_SCRIPT) < run.index(PLAYWRIGHT_INSTALL), where


def test_script_de_espelho_so_e_chamado_pelo_script_de_instalacao() -> None:
    assert not [where for where, step in _all_steps() if MIRROR_SCRIPT in step.get("run", "")]
    assert "ci-apt-sem-azure.sh" in (ROOT / INSTALL_SCRIPT).read_text()


def test_passo_do_script_tem_teto_acima_de_todas_as_tentativas() -> None:
    default_s = _script_default("CI_APT_TENTATIVA_S")
    steps = [(w, s) for w, s in _all_steps() if INSTALL_SCRIPT in s.get("run", "")]

    assert steps
    for where, step in steps:
        attempt_s = int(step.get("env", {}).get("CI_APT_TENTATIVA_S", default_s))
        attempts_s = _mirror_count() * attempt_s
        assert step["timeout-minutes"] * 60 >= attempts_s + INSTALL_MARGIN_S, where


def _run_playwright_step(step: Step, tmp_path: Path, lista: str) -> list[str]:
    """Roda o `run:` do passo em web/ com o script de pacotes, o de instalação e o npx falsos."""
    scripts = tmp_path / "scripts"
    web = tmp_path / "web"
    bin_dir = tmp_path / "bin"
    for directory in (scripts, web, bin_dir):
        directory.mkdir()
    for fake in (
        scripts / "ci-playwright-pacotes.sh",
        scripts / "ci-apt-instalar.sh",
        bin_dir / "npx",
    ):
        fake.write_text(FAKE_SCRIPT)
        fake.chmod(0o755)
    log = tmp_path / "chamadas"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "FAKE_LISTA": lista,
        "DEBS": "/tmp/debs",  # noqa: S108 - só um argumento repassado ao script falso
    }
    result = subprocess.run(  # noqa: S603 - o `run:` do próprio workflow
        [*GITHUB_BASH, step["run"]],
        env=env,
        cwd=web,
        capture_output=True,
        text=True,
        check=False,
        timeout=STEP_TIMEOUT_S,
    )
    assert result.returncode == 0, result.stderr
    return log.read_text().splitlines()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_passo_do_playwright_sem_falta_nao_chama_o_apt(index: int, tmp_path: Path) -> None:
    _, step = _playwright_steps()[index]

    calls = _run_playwright_step(step, tmp_path, lista="")

    assert not any(c.startswith("ci-apt-instalar.sh") for c in calls)
    assert calls[-1].startswith("npx playwright install ")


@pytest.mark.parametrize("index", [0, 1, 2])
def test_passo_do_playwright_instala_exatamente_o_que_falta(index: int, tmp_path: Path) -> None:
    _, step = _playwright_steps()[index]

    calls = _run_playwright_step(step, tmp_path, lista="libnss3\nxvfb\n")

    assert "ci-apt-instalar.sh /tmp/debs libnss3 xvfb" in calls
    assert calls[-1].startswith("npx playwright install ")


def test_os_tres_passos_do_playwright_estao_cobertos() -> None:
    # Dois no ci.yml (job e2e e job e2e-gvisor) e um no e2e-vps.yml.
    assert len(_playwright_steps()) == 3


@pytest.mark.parametrize(("workflow", "job_id"), _install_jobs())
def test_so_job_sem_segredo_e_com_lista_fixa_usa_o_cache_do_apt(workflow: str, job_id: str) -> None:
    job = _jobs(workflow)[job_id]
    uses = [step.get("uses", "") for step in job["steps"]]
    apt_cache = [u for u in uses if u.startswith((CACHE_RESTORE, CACHE_SAVE))]

    if "environment" in job:
        assert not any(u.startswith("actions/cache") for u in uses)
        assert "APT_PACOTES" not in job.get("env", {})
    elif "APT_PACOTES" in job.get("env", {}):
        assert len(apt_cache) == 2
        assert all(PINNED_ACTION.search(u) for u in apt_cache)
    else:
        assert apt_cache == []


def _cached_install_jobs() -> list[tuple[str, str]]:
    return [(w, j) for w, j in _install_jobs() if "APT_PACOTES" in _jobs(w)[j].get("env", {})]


@pytest.mark.parametrize("workflow", ["ci.yml"])
def test_verificacoes_e_gvisor_usam_o_cache(workflow: str) -> None:
    assert {j for w, j in _cached_install_jobs() if w == workflow} == {"checks", "gvisor"}


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


def test_script_do_runsc_e_executavel() -> None:
    assert (ROOT / RUNSC_SCRIPT).stat().st_mode & 0o111


def test_script_de_pacotes_do_playwright_e_executavel() -> None:
    assert (ROOT / PLAYWRIGHT_PACKAGES_SCRIPT).stat().st_mode & 0o111
