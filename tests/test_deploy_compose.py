"""deploy/release-env.sh e deploy/compose.sh: a versão implantada fica num arquivo do host, e o
operador roda o Compose de produção com o mesmo comando que o deploy usa.

Roda os scripts de verdade. O compose.sh é copiado para uma árvore temporária com o mesmo layout
de /srv/goblinshift (app/deploy/compose.sh e secrets/), com um `docker` falso no PATH que registra
os argumentos e as variáveis que recebeu.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RELEASE_ENV_SCRIPT = ROOT / "deploy" / "release-env.sh"
COMPOSE_SCRIPT = ROOT / "deploy" / "compose.sh"
TAG = "sha-0123456789ab"
EXECUTOR_IMAGE = f"ghcr.io/bipoxdxd/goblinshift-executor@sha256:{'a' * 64}"
SCRIPT_TIMEOUT_SECONDS = 30
RELEASE_ENV_MODE = 0o600
FAKE_DOCKER_RC = 7

FAKE_DOCKER = """#!/bin/sh
for arg in "$@"; do printf '%s\\n' "$arg"; done >"$FAKE_DOCKER_LOG"
env | grep '^GS_' | sort >"$FAKE_DOCKER_ENV"
pwd >"$FAKE_DOCKER_CWD"
exit "${FAKE_DOCKER_RC:-0}"
"""


def run(
    args: list[str], env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["bash", *args],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
        timeout=SCRIPT_TIMEOUT_SECONDS,
    )


# --- release-env.sh ---------------------------------------------------------------------------


@pytest.fixture
def release_env(tmp_path: Path) -> Path:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    return secrets / "release.env"


def write_release_env(
    release_env: Path, tag: str = TAG, executor_image: str = EXECUTOR_IMAGE
) -> subprocess.CompletedProcess[str]:
    return run([str(RELEASE_ENV_SCRIPT), str(release_env), tag, executor_image])


def test_grava_a_tag_e_a_imagem_do_executor_implantadas(release_env: Path) -> None:
    result = write_release_env(release_env)

    assert result.returncode == 0, result.stderr
    assert release_env.read_text() == (f"GS_IMAGE_TAG={TAG}\nGS_EXECUTOR_IMAGE={EXECUTOR_IMAGE}\n")


def test_arquivo_legivel_so_pelo_dono_e_sem_temporarios(release_env: Path) -> None:
    write_release_env(release_env)

    assert stat.S_IMODE(release_env.stat().st_mode) == RELEASE_ENV_MODE
    assert sorted(p.name for p in release_env.parent.iterdir()) == ["release.env"]


def test_substitui_a_versao_anterior(release_env: Path) -> None:
    release_env.write_text("GS_IMAGE_TAG=sha-ffffffffffff\n")

    write_release_env(release_env)

    assert f"GS_IMAGE_TAG={TAG}\n" in release_env.read_text()
    assert "sha-ffffffffffff" not in release_env.read_text()


@pytest.mark.parametrize(
    ("tag", "executor_image"),
    [
        ("", EXECUTOR_IMAGE),
        ("main", EXECUTOR_IMAGE),
        (f"{TAG}\nGS_ENVIRONMENT=development", EXECUTOR_IMAGE),
        (TAG, ""),
        (TAG, "ghcr.io/bipoxdxd/goblinshift-executor:latest"),
        (TAG, f"ghcr.io/bipoxdxd/outra@sha256:{'a' * 64}"),
        (TAG, f"{EXECUTOR_IMAGE}\nGS_ENVIRONMENT=development"),
    ],
    ids=[
        "tag-vazia",
        "tag-fora-do-formato",
        "tag-com-quebra-de-linha",
        "imagem-vazia",
        "imagem-sem-digest",
        "imagem-de-outro-repositorio",
        "imagem-com-quebra-de-linha",
    ],
)
def test_recusa_valor_invalido_sem_alterar_o_arquivo(
    release_env: Path, tag: str, executor_image: str
) -> None:
    release_env.write_text("anterior\n")

    result = write_release_env(release_env, tag, executor_image)

    assert result.returncode != 0
    assert release_env.read_text() == "anterior\n"
    assert sorted(p.name for p in release_env.parent.iterdir()) == ["release.env"]


# --- compose.sh -------------------------------------------------------------------------------


@pytest.fixture
def host_root(tmp_path: Path) -> Path:
    """Árvore com o layout de /srv/goblinshift, já com um deploy gravado."""
    root = tmp_path / "srv"
    deploy = root / "app" / "deploy"
    deploy.mkdir(parents=True)
    shutil.copy(COMPOSE_SCRIPT, deploy / "compose.sh")
    secrets = root / "secrets"
    secrets.mkdir()
    (secrets / "app.env").write_text("OPENOBSERVE_ROOT_EMAIL=x\n")
    (secrets / "release.env").write_text(f"GS_IMAGE_TAG={TAG}\n")
    return root


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(stat.S_IRWXU)
    return bin_dir


def run_compose(
    host_root: Path, fake_bin: Path, args: list[str], *, docker_rc: int = 0
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(fake_bin / "args.log"),
        "FAKE_DOCKER_ENV": str(fake_bin / "env.log"),
        "FAKE_DOCKER_CWD": str(fake_bin / "cwd.log"),
        "FAKE_DOCKER_RC": str(docker_rc),
    }
    return run([str(host_root / "app" / "deploy" / "compose.sh"), *args], env, cwd=fake_bin)


def docker_args(fake_bin: Path) -> list[str]:
    return (fake_bin / "args.log").read_text().splitlines()


def test_monta_o_compose_de_producao_com_os_dois_arquivos_de_ambiente(
    host_root: Path, fake_bin: Path
) -> None:
    result = run_compose(host_root, fake_bin, ["ps"])

    assert result.returncode == 0, result.stderr
    assert docker_args(fake_bin) == [
        "compose",
        "--env-file",
        f"{host_root}/secrets/app.env",
        "--env-file",
        f"{host_root}/secrets/release.env",
        "-f",
        f"{host_root}/app/compose.yaml",
        "-f",
        f"{host_root}/app/compose.prod.yaml",
        "ps",
    ]


def test_define_o_ambiente_e_os_diretorios_de_producao(host_root: Path, fake_bin: Path) -> None:
    run_compose(host_root, fake_bin, ["ps"])

    assert (fake_bin / "env.log").read_text().splitlines() == [
        "GS_ENVIRONMENT=production",
        f"GS_PG_DATA={host_root}/pg/data",
        f"GS_SECRETS_DIR={host_root}/secrets",
        f"GS_STORAGE_DATA={host_root}/storage",
        f"GS_TELEMETRY_DATA={host_root}/telemetry/data",
    ]


def test_roda_o_compose_no_checkout_seja_qual_for_o_diretorio_de_quem_chama(
    host_root: Path, fake_bin: Path
) -> None:
    # Na VPS, `sudo -u goblinshift` herda o diretório de quem chamou, que esse usuário pode não
    # conseguir ler; o Compose faz stat no diretório atual e falharia.
    run_compose(host_root, fake_bin, ["ps"])

    assert (fake_bin / "cwd.log").read_text().strip() == str(host_root / "app")


def test_repassa_os_argumentos_do_operador_sem_reinterpretar(
    host_root: Path, fake_bin: Path
) -> None:
    args = ["exec", "api", "gs-admin", "create-user", "--display-name", "Ana Maria; ls *"]

    run_compose(host_root, fake_bin, args)

    assert docker_args(fake_bin)[-len(args) :] == args


def test_devolve_o_codigo_de_saida_do_compose(host_root: Path, fake_bin: Path) -> None:
    result = run_compose(host_root, fake_bin, ["ps"], docker_rc=FAKE_DOCKER_RC)

    assert result.returncode == FAKE_DOCKER_RC


def test_sem_deploy_gravado_falha_antes_de_chamar_o_compose(
    host_root: Path, fake_bin: Path
) -> None:
    (host_root / "secrets" / "release.env").unlink()

    result = run_compose(host_root, fake_bin, ["ps"])

    assert result.returncode != 0
    assert "release.env" in result.stderr
    assert not (fake_bin / "args.log").exists()
