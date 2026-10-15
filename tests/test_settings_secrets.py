"""Configuração e segredos da identidade web (docs/GoblinShiftPlan.md, 3.6; fatia 6).

Em produção, a API não sobe sem qualquer segredo ou identificador de que o login dependa: a falha
aparece no deploy, e não no primeiro login. Fora de produção, as chaves do servidor têm um valor
de desenvolvimento, e o provedor sem cliente configurado fica desligado.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import (
    MissingSecretError,
    Settings,
    WebConfigError,
)

SECRET = "s" * 32
EXECUTOR_IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
ORIGIN = "https://goblinshift.dev"
BOOT_TIMEOUT_SECONDS = 60

SECRET_FILES = (
    "google_client_secret_file",
    "github_client_secret_file",
    "csrf_secret_file",
    "login_cookie_secret_file",
)
CLIENT_IDS = ("google_client_id", "github_client_id")


def _secret_file(tmp_path: Path, name: str, value: str) -> Path:
    path = tmp_path / name
    path.write_text(value + "\n")
    return path


def _production(tmp_path: Path, **overrides: Any) -> Settings:
    """Configuração de produção completa; cada teste tira ou troca uma peça."""
    values: dict[str, Any] = {
        "environment": "production",
        "db_port": 1,
        "cursor_secret": SecretStr(SECRET),
        "user_hash_secret": SecretStr(SECRET),
        "runner": "executor_ctl",
        "executor_image": EXECUTOR_IMAGE,
        "public_origin": ORIGIN,
        "google_client_id": "id-google.apps.googleusercontent.com",
        "github_client_id": "id-github",
        "google_client_secret_file": _secret_file(tmp_path, "google", "segredo-google"),
        "github_client_secret_file": _secret_file(tmp_path, "github", "segredo-github"),
        "csrf_secret_file": _secret_file(tmp_path, "csrf", "c" * 64),
        "login_cookie_secret_file": _secret_file(tmp_path, "login", "l" * 64),
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_producao_completa_sobe(tmp_path: Path) -> None:
    app = create_app(_production(tmp_path))

    assert app.state.web_identity.public_origin == ORIGIN


def test_producao_le_segredos_dos_arquivos_sem_a_quebra_de_linha(tmp_path: Path) -> None:
    web = _production(tmp_path).web_identity()

    assert web.google is not None
    assert web.github is not None
    secrets = (web.google.client_secret, web.github.client_secret)
    assert secrets == ("segredo-google", "segredo-github")
    assert web.csrf_key == b"c" * 64
    assert web.login_cookie_key == b"l" * 64


@pytest.mark.parametrize("missing", SECRET_FILES)
def test_producao_sem_segredo_nao_sobe(tmp_path: Path, missing: str) -> None:
    with pytest.raises(MissingSecretError, match=f"GS_{missing.upper()}"):
        create_app(_production(tmp_path, **{missing: None}))


@pytest.mark.parametrize("missing", SECRET_FILES)
def test_arquivo_de_segredo_inexistente_nao_sobe(tmp_path: Path, missing: str) -> None:
    with pytest.raises(MissingSecretError, match=f"GS_{missing.upper()}"):
        _production(tmp_path, **{missing: tmp_path / "nao-existe"}).web_identity()


@pytest.mark.parametrize("missing", SECRET_FILES)
def test_arquivo_de_segredo_vazio_nao_sobe(tmp_path: Path, missing: str) -> None:
    empty = _secret_file(tmp_path, "vazio", "  ")

    with pytest.raises(MissingSecretError, match=f"GS_{missing.upper()}"):
        _production(tmp_path, **{missing: empty}).web_identity()


@pytest.mark.skipif(os.geteuid() == 0, reason="root lê qualquer arquivo")
@pytest.mark.parametrize("missing", SECRET_FILES)
def test_arquivo_de_segredo_ilegivel_nao_sobe_e_diz_qual(tmp_path: Path, missing: str) -> None:
    """No host o arquivo precisa de modo 644: com 600 o UID da API não o lê (README)."""
    unreadable = _secret_file(tmp_path, "ilegivel", "s" * 64)
    unreadable.chmod(0)

    with pytest.raises(MissingSecretError, match=f"GS_{missing.upper()}.*644"):
        _production(tmp_path, **{missing: unreadable}).web_identity()


@pytest.mark.parametrize("missing", SECRET_FILES)
def test_caminho_de_segredo_que_e_diretorio_nao_sobe(tmp_path: Path, missing: str) -> None:
    directory = tmp_path / "diretorio"
    directory.mkdir()

    with pytest.raises(MissingSecretError, match=f"GS_{missing.upper()}"):
        _production(tmp_path, **{missing: directory}).web_identity()


def test_boot_real_sem_segredo_sai_com_erro_que_nomeia_a_variavel(tmp_path: Path) -> None:
    """O mesmo comando da imagem: o processo termina, e a última linha diz o que falta."""
    settings = _production(tmp_path, github_client_secret_file=tmp_path / "nao-existe")
    env = {
        "PATH": os.environ["PATH"],
        "GS_ENVIRONMENT": "production",
        "GS_CURSOR_SECRET": SECRET,
        "GS_USER_HASH_SECRET": SECRET,
        "GS_RUNNER": "executor_ctl",
        "GS_EXECUTOR_IMAGE": EXECUTOR_IMAGE,
        "GS_PUBLIC_ORIGIN": ORIGIN,
        "GS_GOOGLE_CLIENT_ID": "id-google",
        "GS_GITHUB_CLIENT_ID": "id-github",
        "GS_GOOGLE_CLIENT_SECRET_FILE": str(settings.google_client_secret_file),
        "GS_GITHUB_CLIENT_SECRET_FILE": str(settings.github_client_secret_file),
        "GS_CSRF_SECRET_FILE": str(settings.csrf_secret_file),
        "GS_LOGIN_COOKIE_SECRET_FILE": str(settings.login_cookie_secret_file),
    }

    result = subprocess.run(
        [sys.executable, "-m", "uvicorn", "goblinshift.entrypoints.api.app:create_app",
         "--factory", "--host", "127.0.0.1", "--port", "0"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=BOOT_TIMEOUT_SECONDS,
    )  # fmt: skip

    assert result.returncode != 0
    last_line = result.stderr.strip().splitlines()[-1]
    assert last_line.startswith("goblinshift.platform.settings.MissingSecretError:")
    assert "GS_GITHUB_CLIENT_SECRET_FILE" in last_line


@pytest.mark.parametrize("key_file", ["csrf_secret_file", "login_cookie_secret_file"])
def test_chave_do_servidor_curta_nao_sobe(tmp_path: Path, key_file: str) -> None:
    short = _secret_file(tmp_path, "curta", "k" * 31)

    with pytest.raises(WebConfigError, match="32"):
        _production(tmp_path, **{key_file: short}).web_identity()


def test_chave_do_servidor_com_32_caracteres_e_aceita(tmp_path: Path) -> None:
    exact = _secret_file(tmp_path, "exata", "k" * 32)

    web = _production(tmp_path, csrf_secret_file=exact).web_identity()

    assert web.csrf_key == b"k" * 32


@pytest.mark.parametrize("missing", CLIENT_IDS)
def test_producao_sem_id_de_cliente_nao_sobe(tmp_path: Path, missing: str) -> None:
    with pytest.raises(WebConfigError, match=f"GS_{missing.upper()}"):
        create_app(_production(tmp_path, **{missing: None}))


def test_producao_sem_origem_publica_nao_sobe(tmp_path: Path) -> None:
    with pytest.raises(WebConfigError, match="GS_PUBLIC_ORIGIN"):
        create_app(_production(tmp_path, public_origin=None))


def test_producao_com_origem_http_nao_sobe(tmp_path: Path) -> None:
    with pytest.raises(WebConfigError, match="https"):
        _production(tmp_path, public_origin="http://goblinshift.dev").web_identity()


@pytest.mark.parametrize(
    "origin",
    [
        "goblinshift.dev",
        "https://",
        "https://goblinshift.dev/",
        "https://goblinshift.dev/app",
        "https://goblinshift.dev?x=1",
        "https://goblinshift.dev#x",
        "https://user@goblinshift.dev",
        "ftp://goblinshift.dev",
    ],
)
def test_origem_publica_fora_do_formato_e_recusada(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(public_origin=origin)


@pytest.mark.parametrize("origin", ["https://goblinshift.dev", "http://localhost:8080"])
def test_origem_publica_no_formato_e_aceita(origin: str) -> None:
    assert Settings(public_origin=origin).web_identity().public_origin == origin


@pytest.mark.parametrize("environment", ["development", "test"])
def test_fora_de_producao_tem_chaves_padrao_e_provedores_desligados(environment: str) -> None:
    web = Settings.model_validate({"environment": environment}).web_identity()

    assert web.public_origin == "http://localhost"
    assert len(web.csrf_key) >= 32
    assert len(web.login_cookie_key) >= 32
    assert web.csrf_key != web.login_cookie_key
    assert web.google is None
    assert web.github is None


def test_fora_de_producao_provedor_com_id_e_segredo_fica_ligado(tmp_path: Path) -> None:
    settings = Settings(
        environment="development",
        github_client_id="id-dev",
        github_client_secret_file=_secret_file(tmp_path, "github", "segredo-dev"),
    )

    web = settings.web_identity()

    assert web.github is not None
    assert web.github.client_id == "id-dev"
    assert web.google is None


def test_provedor_configurado_pela_metade_nao_sobe() -> None:
    with pytest.raises(WebConfigError, match="GS_GOOGLE_CLIENT_SECRET_FILE"):
        Settings(environment="development", google_client_id="id-dev").web_identity()


def test_cadastro_fechado_por_padrao() -> None:
    assert Settings().web_identity().signup_open is False


def test_cadastro_aberto_por_configuracao() -> None:
    assert Settings(signup_mode="open").web_identity().signup_open is True


def test_modo_de_cadastro_desconhecido_e_recusado() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"signup_mode": "invite"})


def test_timeout_dos_provedores_padrao_de_5_segundos() -> None:
    assert Settings().web_identity().oauth_http_timeout_s == 5.0


def test_segredos_nao_aparecem_no_repr(tmp_path: Path) -> None:
    web = _production(tmp_path).web_identity()

    text = repr(web)

    for secret in ("segredo-google", "segredo-github", "c" * 64, "l" * 64):
        assert secret not in text


def test_producao_deixa_o_hsts_para_o_nginx(tmp_path: Path) -> None:
    """O Nginx já manda o HSTS; a API repetir o header daria dois valores na resposta."""
    client = TestClient(create_app(_production(tmp_path)))

    response = client.get("/livez")

    assert response.status_code == 200
    assert "strict-transport-security" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"
