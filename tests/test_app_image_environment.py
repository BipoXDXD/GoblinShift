"""A imagem da aplicação nasce em produção (issue #442, item 54 c; decisão D6 = a).

Sem `GS_ENVIRONMENT`, `Settings` cai no padrão `development` e pula os segredos obrigatórios, a
origem https e o `executor_ctl`. A imagem fecha essa porta com `ENV GS_ENVIRONMENT=production`; o
código mantém o padrão para testes e scripts, e a pilha local declara o próprio ambiente em cada
serviço que roda a imagem. Sem Docker: o Dockerfile é lido como texto e o processo roda no venv
com o mesmo ambiente que a imagem daria.
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_compose_secrets import BASE_ENV, ROOT, compose_config

DOCKERFILE_NAME = "docker/app.Dockerfile"
DOCKERFILE = ROOT / DOCKERFILE_NAME
SRC = str(ROOT / "src")
APP_IMAGE = "ghcr.io/bipoxdxd/goblinshift:"
WORKER_COMMAND = (sys.executable, "-m", "goblinshift.entrypoints.worker")
EXIT_CONFIGURATION = 2
PROCESS_TIMEOUT_SECONDS = 60
# Combinações que rodam a imagem fora da VPS: a pilha local e as dos e2e do CI.
LOCAL_STACKS = (
    ("compose.yaml",),
    ("compose.yaml", "compose.override.yaml"),
    ("compose.yaml", "compose.e2e-gvisor.yaml"),
    ("compose.yaml", "compose.e2e-release.yaml"),
)
CI_APP_IMAGE = f"127.0.0.1:5000/goblinshift@sha256:{'b' * 64}"
OVERLAY_ENV = {
    "GS_EXECUTOR_IMAGE": f"ghcr.io/bipoxdxd/goblinshift-executor@sha256:{'a' * 64}",
    "CI_APP_IMAGE": CI_APP_IMAGE,
    "CI_WEB_IMAGE": f"127.0.0.1:5000/goblinshift-web@sha256:{'c' * 64}",
}


def runs_app_image(service: dict[str, Any]) -> bool:
    """Serviço que roda a imagem da aplicação: pela tag, pelo digest do CI ou pelo build."""
    image: str = service.get("image", "")
    dockerfile: str = service.get("build", {}).get("dockerfile", "")
    return image.startswith(APP_IMAGE) or image == CI_APP_IMAGE or dockerfile == DOCKERFILE_NAME


def final_stage_env(dockerfile: Path) -> dict[str, str]:
    """`ENV` do último estágio (depois do último `FROM`), com as continuações de linha unidas."""
    text = dockerfile.read_text(encoding="utf-8").replace("\\\n", " ")
    lines = [line.strip() for line in text.splitlines()]
    last_from = max(i for i, line in enumerate(lines) if line.upper().startswith("FROM "))
    env: dict[str, str] = {}
    for line in lines[last_from + 1 :]:
        if line.upper().startswith("ENV "):
            for pair in shlex.split(line[4:]):
                name, _, value = pair.partition("=")
                env[name] = value
    return env


def test_imagem_define_producao_no_estagio_final() -> None:
    assert final_stage_env(DOCKERFILE)["GS_ENVIRONMENT"] == "production"


@pytest.mark.parametrize("files", LOCAL_STACKS, ids=lambda files: "+".join(files))
def test_todo_servico_do_compose_que_usa_a_imagem_define_o_ambiente(
    files: tuple[str, ...],
) -> None:
    # Sem GS_ENVIRONMENT no shell: o padrão vem do próprio Compose, e não da imagem.
    result = compose_config(files, {**BASE_ENV, **OVERLAY_ENV})

    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    app_services = {name: service for name, service in services.items() if runs_app_image(service)}
    assert app_services
    for name, service in app_services.items():
        assert service.get("environment", {}).get("GS_ENVIRONMENT") == "development", name


def test_processo_com_o_ambiente_da_imagem_e_sem_variaveis_nao_sobe() -> None:
    env = {
        **final_stage_env(DOCKERFILE),
        "PATH": os.environ["PATH"],
        "PYTHONPATH": SRC,
        # Porta sem banco: se o processo pulasse as validações, cairia na conexão, com saída 1.
        "GS_DB_PORT": "1",
    }

    result = subprocess.run(  # noqa: S603 - comando montado pelo teste, sem shell
        WORKER_COMMAND,
        capture_output=True,
        text=True,
        check=False,
        timeout=PROCESS_TIMEOUT_SECONDS,
        env=env,
        cwd=ROOT,
    )

    assert result.returncode == EXIT_CONFIGURATION, result.stdout + result.stderr
    assert "produção" in result.stdout + result.stderr
