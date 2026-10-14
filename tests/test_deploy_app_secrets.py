"""deploy/app-secrets.sh: os segredos da aplicação estão no lugar antes do deploy seguir.

Gera, uma vez, os que a própria aplicação cria: no `app.env`, o do cursor (3.3) e o do
`user.hash` (3.7); em arquivo, as chaves do CSRF e do cookie de login (fatia 6, 3.6). Confere os
que o operador grava (os client secrets do Google e do GitHub). Roda o script de verdade sobre um
diretório temporário com o layout de /srv/goblinshift/secrets, como o release.sh faz na VPS.
"""

import re
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "app-secrets.sh"
GENERATED = ("GS_CURSOR_SECRET", "GS_USER_HASH_SECRET")
GENERATED_FILES = ("csrf_secret", "login_cookie_secret")
OPERATOR_FILES = ("google_client_secret", "github_client_secret")
HEX_SECRET = re.compile(r"[0-9a-f]{64}")
# O container lê com outro UID, dentro do diretório 700 (README, "Segredos do login").
SECRET_FILE_MODE = 0o644
OWNER_ONLY_MODE = 0o600
SCRIPT_TIMEOUT_SECONDS = 30


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "secrets"
    directory.mkdir()
    for name in OPERATOR_FILES:
        path = directory / name
        path.write_text(f"segredo-do-operador-{name}")
        path.chmod(SECRET_FILE_MODE)
    return directory


@pytest.fixture
def env_file(secrets_dir: Path) -> Path:
    path = secrets_dir / "app.env"
    path.write_text("")
    return path


def run_script(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["bash", str(SCRIPT), str(env_file)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_SECONDS,
    )


def ensure_secrets(env_file: Path) -> None:
    result = run_script(env_file)
    assert result.returncode == 0, result.stderr


def entries(env_file: Path) -> dict[str, str]:
    lines = env_file.read_text().splitlines()
    return dict(line.split("=", 1) for line in lines)


# --- app.env ----------------------------------------------------------------------------------


def test_arquivo_sem_quebra_de_linha_final_nao_gruda_o_segredo_na_ultima_linha(
    env_file: Path,
) -> None:
    env_file.write_text("GS_SMTP_HOST=smtp.example.com")

    ensure_secrets(env_file)

    values = entries(env_file)
    assert values["GS_SMTP_HOST"] == "smtp.example.com"
    for name in GENERATED:
        assert HEX_SECRET.fullmatch(values[name]), name
    assert env_file.read_text().endswith("\n")


def test_segredo_existente_e_mantido_e_rodar_de_novo_nao_duplica(env_file: Path) -> None:
    env_file.write_text(f"GS_CURSOR_SECRET={'c' * 64}\n")

    ensure_secrets(env_file)
    first = env_file.read_text()
    ensure_secrets(env_file)

    assert env_file.read_text() == first
    assert entries(env_file)["GS_CURSOR_SECRET"] == "c" * 64
    assert sorted(entries(env_file)) == sorted(GENERATED)


def test_arquivo_vazio_recebe_os_dois_segredos(env_file: Path) -> None:
    ensure_secrets(env_file)

    assert sorted(entries(env_file)) == sorted(GENERATED)
    assert env_file.read_text().splitlines()[0].startswith("GS_")


# --- chaves do servidor em arquivo ------------------------------------------------------------


@pytest.mark.parametrize("name", GENERATED_FILES)
def test_cria_a_chave_do_servidor_com_256_bits_legivel_pela_api(
    env_file: Path, secrets_dir: Path, name: str
) -> None:
    ensure_secrets(env_file)

    path = secrets_dir / name
    assert HEX_SECRET.fullmatch(path.read_text()), name
    assert stat.S_IMODE(path.stat().st_mode) == SECRET_FILE_MODE


def test_chaves_do_servidor_sao_diferentes_entre_si(env_file: Path, secrets_dir: Path) -> None:
    ensure_secrets(env_file)

    values = {(secrets_dir / name).read_text() for name in GENERATED_FILES}
    assert len(values) == len(GENERATED_FILES)


@pytest.mark.parametrize("name", GENERATED_FILES)
def test_chave_do_servidor_existente_nunca_e_trocada(
    env_file: Path, secrets_dir: Path, name: str
) -> None:
    path = secrets_dir / name
    path.write_text("k" * 64)
    path.chmod(SECRET_FILE_MODE)

    ensure_secrets(env_file)
    ensure_secrets(env_file)

    assert path.read_text() == "k" * 64


@pytest.mark.parametrize("name", GENERATED_FILES)
def test_chave_do_servidor_vazia_e_gerada(env_file: Path, secrets_dir: Path, name: str) -> None:
    (secrets_dir / name).write_text("")

    ensure_secrets(env_file)

    assert HEX_SECRET.fullmatch((secrets_dir / name).read_text()), name


def test_so_os_arquivos_esperados_ficam_no_diretorio(env_file: Path, secrets_dir: Path) -> None:
    ensure_secrets(env_file)

    expected = {"app.env", *GENERATED_FILES, *OPERATOR_FILES}
    assert {path.name for path in secrets_dir.iterdir()} == expected


# --- segredos gravados pelo operador ----------------------------------------------------------


@pytest.mark.parametrize("name", OPERATOR_FILES)
def test_client_secret_ausente_para_o_deploy_e_diz_qual_falta(
    env_file: Path, secrets_dir: Path, name: str
) -> None:
    (secrets_dir / name).unlink()

    result = run_script(env_file)

    assert result.returncode != 0
    assert name in result.stderr


@pytest.mark.parametrize("name", OPERATOR_FILES)
def test_client_secret_vazio_para_o_deploy(env_file: Path, secrets_dir: Path, name: str) -> None:
    (secrets_dir / name).write_text("")

    result = run_script(env_file)

    assert result.returncode != 0
    assert name in result.stderr


@pytest.mark.parametrize("name", OPERATOR_FILES)
def test_client_secret_que_a_api_nao_le_para_o_deploy(
    env_file: Path, secrets_dir: Path, name: str
) -> None:
    (secrets_dir / name).chmod(OWNER_ONLY_MODE)

    result = run_script(env_file)

    assert result.returncode != 0
    assert name in result.stderr
    assert "644" in result.stderr


def test_mensagem_de_erro_nunca_traz_o_conteudo_de_um_segredo(
    env_file: Path, secrets_dir: Path
) -> None:
    canary = f"CANARY-{uuid.uuid4()}"
    path = secrets_dir / "google_client_secret"
    path.write_text(canary)
    path.chmod(OWNER_ONLY_MODE)

    result = run_script(env_file)

    assert result.returncode != 0
    assert canary not in result.stderr
    assert canary not in result.stdout
