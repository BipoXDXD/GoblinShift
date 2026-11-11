"""Job da CI que roda o Playwright contra a API real com cada tentativa sob gVisor.

O job só prova o que promete se a pilha for a de produção onde importa (Runner, socket, área de
trabalho) e se cada tentativa tiver mesmo rodado sob runsc. Os testes abaixo seguram essas duas
condições e o vínculo entre o workflow e o gate da publicação, que recusa job fora da lista.
"""

import importlib.util
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
GATE = ROOT / "deploy" / "coolify" / "release" / "gate.py"
PRODUCTION_COMPOSE = ROOT / "compose.prod.yaml"
GVISOR_COMPOSE = ROOT / "compose.e2e-gvisor.yaml"
SCRIPT = ROOT / "scripts" / "e2e-gvisor.sh"
RUNSC_INSTALLER = "scripts/ci-install-runsc.sh"
JOB_ID = "e2e-gvisor"
JOB_NAME = "Jornadas reais sob gVisor (Playwright)"
EXECUTOR_CLIENTS = ("worker", "maintenance")
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def jobs() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = yaml.safe_load(CI_WORKFLOW.read_text())["jobs"]
    return found


def job() -> dict[str, Any]:
    return jobs()[JOB_ID]


def run_commands(definition: dict[str, Any]) -> list[str]:
    return [step["run"] for step in definition["steps"] if "run" in step]


def services(compose_file: Path) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = yaml.safe_load(compose_file.read_text())["services"]
    return found


def release_gate() -> Any:
    spec = importlib.util.spec_from_file_location("release_gate", GATE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_o_gate_conhece_exatamente_os_jobs_do_workflow() -> None:
    # O gate recusa nome faltando ou sobrando: um job novo só no workflow travaria a publicação
    # depois do merge, e não no PR.
    gate = release_gate()

    names = {definition["name"] for definition in jobs().values()}

    assert names == gate.REQUIRED["goblinshift"] | gate.LEGACY_SKIPS


def test_o_job_e_obrigatorio_para_publicar() -> None:
    assert JOB_NAME in release_gate().REQUIRED["goblinshift"]


def test_o_job_roda_em_paralelo_e_com_teto_de_tempo() -> None:
    assert job()["name"] == JOB_NAME
    # Paralelo, como o do OpenObserve: fora do orçamento de 5 min do commit stage.
    assert "needs" not in job()
    assert job()["timeout-minutes"] > 0


def test_o_job_roda_em_todo_pr_e_em_todo_push() -> None:
    assert "if" not in job()


def test_o_job_nao_recebe_segredo_nem_ambiente_nem_permissao_a_mais() -> None:
    assert "secrets." not in yaml.safe_dump(job())
    assert "environment" not in job()
    # Herda o `contents: read` do workflow.
    assert "permissions" not in job()


def test_o_job_nao_guarda_credencial_do_git() -> None:
    checkouts = [
        step for step in job()["steps"] if step.get("uses", "").startswith("actions/checkout@")
    ]

    assert len(checkouts) == 1
    assert checkouts[0]["with"]["persist-credentials"] is False


def test_o_job_fixa_toda_action_por_sha() -> None:
    actions = [step["uses"] for step in job()["steps"] if "uses" in step]

    assert actions
    assert all(PINNED_ACTION.fullmatch(action) for action in actions), actions


@pytest.mark.parametrize("job_id", ["gvisor", JOB_ID])
def test_os_dois_jobs_instalam_o_gvisor_do_mesmo_jeito(job_id: str) -> None:
    assert RUNSC_INSTALLER in run_commands(jobs()[job_id])


def test_o_instalador_do_runsc_atualiza_so_a_fonte_do_gvisor() -> None:
    # Os espelhos do Ubuntu já prenderam o `apt-get update` por 50 min; o runsc vem de uma fonte
    # própria, e o teste dos espelhos (tests/test_ci_apt_mirror.py) lê este script também.
    installer = (ROOT / RUNSC_INSTALLER).read_text()

    updates = [line for line in installer.splitlines() if "apt-get update" in line]
    assert updates
    assert all("Dir::Etc::sourceparts=-" in line for line in updates)


def test_o_job_nao_usa_o_apt_do_playwright() -> None:
    # `playwright install --with-deps` chama o apt por dentro, sem teto: as dependências vêm do
    # script com teto por tentativa, e o navegador entra depois.
    commands = run_commands(job())

    assert not any("--with-deps" in command for command in commands)
    browser = next(i for i, command in enumerate(commands) if "npx playwright install" in command)
    assert commands.index(RUNSC_INSTALLER) < browser


def test_o_job_sobe_a_pilha_antes_das_jornadas() -> None:
    commands = run_commands(job())

    position = {command: index for index, command in enumerate(commands)}
    assert (
        position[RUNSC_INSTALLER]
        < position["scripts/e2e-gvisor.sh up"]
        < position["scripts/e2e-gvisor.sh journeys"]
    )


@pytest.mark.parametrize("service", ["api", *EXECUTOR_CLIENTS])
def test_a_pilha_usa_o_runner_de_producao(service: str) -> None:
    environment = services(GVISOR_COMPOSE)[service]["environment"]
    production = services(PRODUCTION_COMPOSE)[service]["environment"]

    assert environment["GS_RUNNER"] == production["GS_RUNNER"] == "executor_ctl"
    assert environment["GS_EXECUTOR_IMAGE"].startswith("${GS_EXECUTOR_IMAGE:?")


@pytest.mark.parametrize("service", EXECUTOR_CLIENTS)
@pytest.mark.parametrize("key", ["group_add", "volumes"])
def test_worker_e_manutencao_falam_com_o_host_como_em_producao(service: str, key: str) -> None:
    assert services(GVISOR_COMPOSE)[service][key] == services(PRODUCTION_COMPOSE)[service][key]


@pytest.mark.parametrize("service", EXECUTOR_CLIENTS)
def test_a_area_de_trabalho_e_a_de_producao(service: str) -> None:
    gvisor = services(GVISOR_COMPOSE)[service]["environment"]["GS_WORK_ROOT"]

    assert gvisor == services(PRODUCTION_COMPOSE)[service]["environment"]["GS_WORK_ROOT"]


def test_so_o_nginx_e_publicado_e_so_no_loopback() -> None:
    published = {
        name: service["ports"]
        for name, service in services(GVISOR_COMPOSE).items()
        if "ports" in service
    }

    assert published == {"nginx": ["127.0.0.1:80:80"]}


@pytest.fixture
def fake_docker(tmp_path: Path) -> Path:
    """`docker` falso que responde o número de tentativas gravado no banco."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('#!/bin/sh\ncat "$FAKE_ATTEMPTS"\n')
    docker.chmod(0o755)
    return bin_dir


def verify(fake_docker: Path, tmp_path: Path, recorded: str, attempts: str) -> int:
    runtimes = tmp_path / "runtimes.txt"
    runtimes.write_text(recorded)
    attempts_file = tmp_path / "attempts.txt"
    attempts_file.write_text(attempts)
    completed = subprocess.run(  # noqa: S603 - argv fixo
        ["bash", str(SCRIPT), "verify", str(runtimes)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={
            **os.environ,
            "PATH": f"{fake_docker}:{os.environ['PATH']}",
            "FAKE_ATTEMPTS": str(attempts_file),
        },
    )
    return completed.returncode


def test_verify_aceita_toda_tentativa_sob_runsc(fake_docker: Path, tmp_path: Path) -> None:
    assert verify(fake_docker, tmp_path, "runsc\nrunsc\n", "2\n") == 0


def test_verify_recusa_rodada_sem_tentativa(fake_docker: Path, tmp_path: Path) -> None:
    # Sem esta recusa, o job ficaria verde sem nenhuma receita ter passado pelo gVisor.
    assert verify(fake_docker, tmp_path, "", "0\n") != 0


@pytest.mark.parametrize("other", ["runc", "", "desconhecido"])
def test_verify_recusa_tentativa_fora_do_runsc(
    fake_docker: Path, tmp_path: Path, other: str
) -> None:
    assert verify(fake_docker, tmp_path, f"runsc\n{other}\n", "2\n") != 0


@pytest.mark.parametrize(
    ("recorded", "attempts"),
    [("runsc\n", "2\n"), ("runsc\nrunsc\n", "1\n"), ("runsc\nrunsc\nrunc\n", "2\n")],
)
def test_verify_recusa_contagem_diferente_do_banco(
    fake_docker: Path, tmp_path: Path, recorded: str, attempts: str
) -> None:
    assert verify(fake_docker, tmp_path, recorded, attempts) != 0


@pytest.mark.parametrize("command", ["up", "journeys"])
def test_o_script_recusa_rodar_fora_do_runner_efemero(command: str, tmp_path: Path) -> None:
    # Ele escreve em /etc, /var/lib e /srv e instala um serviço: na VPS seria um desastre.
    environment = {key: value for key, value in os.environ.items() if key != "CI"}

    completed = subprocess.run(  # noqa: S603 - argv fixo
        ["bash", str(SCRIPT), command],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        cwd=tmp_path,
        env=environment,
    )

    assert completed.returncode != 0
    assert "runner efêmero" in completed.stderr
