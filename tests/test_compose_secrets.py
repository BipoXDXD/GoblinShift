"""Compose de produção: o login web recebe a configuração e os segredos (fatia 6, 3.6).

Lê o `docker compose config` renderizado com os mesmos arquivos do deploy/compose.sh, e não o
YAML cru: assim a herança das âncoras, a fusão dos dois arquivos e a interpolação entram na
prova. Só a API faz login, então só ela recebe os segredos web (menor privilégio). Sem uma
variável obrigatória, o Compose recusa o arquivo antes de qualquer comando, inclusive o
`migrate`, e o deploy para sem mexer no banco.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from goblinshift.platform.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
PROD_FILES = ("compose.yaml", "compose.prod.yaml")
DEV_FILES = ("compose.yaml", "compose.override.yaml")
COMPOSE_TIMEOUT_SECONDS = 60
MOUNT_ROOT = "/run/secrets"
WEB_SECRETS = {
    "google_client_secret": "GS_GOOGLE_CLIENT_SECRET_FILE",
    "github_client_secret": "GS_GITHUB_CLIENT_SECRET_FILE",
    "csrf_secret": "GS_CSRF_SECRET_FILE",
    "login_cookie_secret": "GS_LOGIN_COOKIE_SECRET_FILE",
}
REQUIRED_WEB_VARS = {
    "GS_PUBLIC_ORIGIN": "https://goblinshift.dev",
    "GS_GOOGLE_CLIENT_ID": "id-google.apps.googleusercontent.com",
    "GS_GITHUB_CLIENT_ID": "id-github",
}
WEB_VARS = {*REQUIRED_WEB_VARS, "GS_SIGNUP_MODE", *WEB_SECRETS.values()}
BASE_ENV = {
    "OPENOBSERVE_BASIC_AUTH": "x",
    "OPENOBSERVE_ROOT_EMAIL": "x@example.com",
    "OPENOBSERVE_ROOT_PASSWORD": "x",
}
PROD_ENV = {
    **BASE_ENV,
    "GS_ENVIRONMENT": "production",
    "GS_IMAGE_TAG": "sha-0123456789ab",
    "GS_EXECUTOR_IMAGE": f"ghcr.io/bipoxdxd/goblinshift-executor@sha256:{'a' * 64}",
    "GS_CURSOR_SECRET": "c" * 64,
    "GS_USER_HASH_SECRET": "u" * 64,
    **REQUIRED_WEB_VARS,
}


def _docker_client_env() -> dict[str, str]:
    """Só o que o cliente do Docker precisa para achar o plugin do Compose e o daemon."""
    return {
        name: value
        for name, value in os.environ.items()
        if name in {"PATH", "HOME"} or name.startswith("DOCKER_")
    }


def compose_config(files: tuple[str, ...], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    file_args = [arg for name in files for arg in ("-f", str(ROOT / name))]
    # Ambiente mínimo e explícito: nada do shell de quem roda o teste entra na interpolação.
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["docker", "compose", "--project-directory", str(ROOT), *file_args,  # noqa: S607
         "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
        timeout=COMPOSE_TIMEOUT_SECONDS,
        env={**_docker_client_env(), **env},
        cwd=ROOT,
    )  # fmt: skip


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "secrets"
    directory.mkdir()
    return directory


@pytest.fixture
def production(secrets_dir: Path) -> dict[str, Any]:
    result = compose_config(PROD_FILES, {**PROD_ENV, "GS_SECRETS_DIR": str(secrets_dir)})
    assert result.returncode == 0, result.stderr
    rendered: dict[str, Any] = json.loads(result.stdout)
    return rendered


def secret_targets(service: dict[str, Any]) -> dict[str, str]:
    return {secret["source"]: secret["target"] for secret in service.get("secrets", [])}


def test_api_recebe_a_origem_publica_e_os_ids_de_cliente(production: dict[str, Any]) -> None:
    environment = production["services"]["api"]["environment"]
    for name, value in REQUIRED_WEB_VARS.items():
        assert environment[name] == value, name


def test_cadastro_fica_fechado_sem_configuracao(production: dict[str, Any]) -> None:
    assert production["services"]["api"]["environment"]["GS_SIGNUP_MODE"] == "closed"


def test_modo_de_cadastro_vem_do_app_env(secrets_dir: Path) -> None:
    env = {**PROD_ENV, "GS_SECRETS_DIR": str(secrets_dir), "GS_SIGNUP_MODE": "open"}
    result = compose_config(PROD_FILES, env)
    assert result.returncode == 0, result.stderr
    api = json.loads(result.stdout)["services"]["api"]
    assert api["environment"]["GS_SIGNUP_MODE"] == "open"


def test_api_monta_cada_segredo_web_no_caminho_da_variavel(production: dict[str, Any]) -> None:
    api = production["services"]["api"]
    targets = secret_targets(api)
    for secret, variable in WEB_SECRETS.items():
        assert targets[secret] == f"{MOUNT_ROOT}/{secret}", secret
        assert api["environment"][variable] == f"{MOUNT_ROOT}/{secret}", variable


def test_segredos_web_vem_de_arquivos_no_diretorio_de_segredos_do_host(
    production: dict[str, Any], secrets_dir: Path
) -> None:
    for secret in WEB_SECRETS:
        assert production["secrets"][secret]["file"] == str(secrets_dir / secret), secret


def test_so_a_api_recebe_os_segredos_e_a_configuracao_do_login(
    production: dict[str, Any],
) -> None:
    for name, service in production["services"].items():
        if name == "api":
            continue
        assert not set(secret_targets(service)) & set(WEB_SECRETS), name
        assert not set(service.get("environment") or {}) & WEB_VARS, name


@pytest.mark.parametrize("missing", sorted(REQUIRED_WEB_VARS))
def test_producao_sem_variavel_do_login_nao_interpola(secrets_dir: Path, missing: str) -> None:
    env = {**PROD_ENV, "GS_SECRETS_DIR": str(secrets_dir)}
    del env[missing]

    result = compose_config(PROD_FILES, env)

    assert result.returncode != 0
    assert missing in result.stderr


def test_producao_sem_diretorio_de_segredos_nao_interpola() -> None:
    result = compose_config(PROD_FILES, PROD_ENV)

    assert result.returncode != 0
    assert "GS_SECRETS_DIR" in result.stderr


def test_ambiente_renderizado_sobe_o_login_com_os_segredos_montados(
    production: dict[str, Any], secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Os nomes do Compose são os que a configuração lê: a API sobe com o ambiente renderizado.

    O `/run/secrets/<nome>` do container é trocado pelo arquivo do host que o Compose monta ali.
    """
    for secret in WEB_SECRETS:
        (secrets_dir / secret).write_text(f"{secret}-{'v' * 32}\n")
    host_file = {
        f"{MOUNT_ROOT}/{secret}": production["secrets"][secret]["file"] for secret in WEB_SECRETS
    }
    for name, value in production["services"]["api"]["environment"].items():
        monkeypatch.setenv(name, host_file.get(value, value))

    web = Settings().web_identity()

    assert web.public_origin == REQUIRED_WEB_VARS["GS_PUBLIC_ORIGIN"]
    assert web.google is not None
    assert web.google.client_secret == f"google_client_secret-{'v' * 32}"
    assert web.github is not None
    assert web.github.client_id == REQUIRED_WEB_VARS["GS_GITHUB_CLIENT_ID"]
    assert web.csrf_key == f"csrf_secret-{'v' * 32}".encode()
    assert not web.signup_open


def test_desenvolvimento_sobe_sem_a_configuracao_do_login() -> None:
    """Fora de produção os provedores ficam desligados e as chaves têm valor de desenvolvimento."""
    result = compose_config(DEV_FILES, BASE_ENV)

    assert result.returncode == 0, result.stderr
    api = json.loads(result.stdout)["services"]["api"]
    assert not set(api["environment"]) & WEB_VARS
    assert set(secret_targets(api)) == {"db_password"}
