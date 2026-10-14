"""deploy/executor-image.sh: resolve o digest da imagem do executor e atualiza a allowlist.

Roda o script de verdade com um `docker` falso no PATH, que responde ao `pull` e ao `image inspect`
conforme o cenário e registra as chamadas.
"""

import json
import os
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "executor-image.sh"
REPO = "ghcr.io/bipoxdxd/goblinshift-executor"
TAG = "sha-0123456789ab"
NEW = f"{REPO}@sha256:{'a' * 64}"
OLD = f"{REPO}@sha256:{'b' * 64}"
OLDER = f"{REPO}@sha256:{'c' * 64}"
OTHER_REPO = f"ghcr.io/bipoxdxd/outra@sha256:{'d' * 64}"
SCRIPT_TIMEOUT_SECONDS = 30
ALLOWLIST_MODE = 0o644

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"
case "$1" in
  pull) exit "${FAKE_PULL_RC:-0}" ;;
  image) printf '%s' "$FAKE_REPO_DIGESTS"; exit "${FAKE_INSPECT_RC:-0}" ;;
esac
exit 99
"""


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(stat.S_IRWXU)
    return bin_dir


@pytest.fixture
def images_file(tmp_path: Path) -> Path:
    executor_dir = tmp_path / "executor"
    executor_dir.mkdir()
    return executor_dir / "images.json"


def run_script(
    fake_bin: Path,
    images_file: Path,
    *,
    repo_digests: Sequence[str] = (NEW,),
    tag: str = TAG,
    pull_rc: int = 0,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(fake_bin / "calls.log"),
        "FAKE_REPO_DIGESTS": "".join(f"{digest}\n" for digest in repo_digests),
        "FAKE_PULL_RC": str(pull_rc),
    }
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["bash", str(SCRIPT), tag, str(images_file)],  # noqa: S607
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=SCRIPT_TIMEOUT_SECONDS,
    )


def write_allowed(images_file: Path, allowed: Sequence[str]) -> None:
    images_file.write_text(json.dumps({"allowed": list(allowed)}), encoding="utf-8")


def read_allowed(images_file: Path) -> list[str]:
    allowed: list[str] = json.loads(images_file.read_text(encoding="utf-8"))["allowed"]
    return allowed


def test_primeiro_deploy_grava_so_o_digest_novo_e_o_imprime(
    fake_bin: Path, images_file: Path
) -> None:
    result = run_script(fake_bin, images_file)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{NEW}\n"
    assert read_allowed(images_file) == [NEW]


def test_baixa_a_imagem_do_executor_pela_tag_implantada(fake_bin: Path, images_file: Path) -> None:
    run_script(fake_bin, images_file)

    calls = (fake_bin / "calls.log").read_text().splitlines()
    assert calls[0] == f"pull --quiet {REPO}:{TAG}"


def test_mantem_o_digest_anterior_depois_do_novo(fake_bin: Path, images_file: Path) -> None:
    write_allowed(images_file, [OLD])

    result = run_script(fake_bin, images_file)

    assert result.returncode == 0, result.stderr
    assert read_allowed(images_file) == [NEW, OLD]


def test_guarda_no_maximo_dois_digests(fake_bin: Path, images_file: Path) -> None:
    write_allowed(images_file, [OLD, OLDER])

    run_script(fake_bin, images_file)

    assert read_allowed(images_file) == [NEW, OLD]


def test_reimplantar_o_mesmo_digest_nao_duplica_nem_perde_o_anterior(
    fake_bin: Path, images_file: Path
) -> None:
    write_allowed(images_file, [NEW, OLD])

    run_script(fake_bin, images_file)

    assert read_allowed(images_file) == [NEW, OLD]


def test_escolhe_o_digest_do_repositorio_do_executor(fake_bin: Path, images_file: Path) -> None:
    result = run_script(fake_bin, images_file, repo_digests=[OTHER_REPO, NEW])

    assert result.stdout == f"{NEW}\n"
    assert read_allowed(images_file) == [NEW]


def test_arquivo_gravado_e_legivel_por_todos_e_sem_temporarios(
    fake_bin: Path, images_file: Path
) -> None:
    run_script(fake_bin, images_file)

    assert stat.S_IMODE(images_file.stat().st_mode) == ALLOWLIST_MODE
    assert sorted(p.name for p in images_file.parent.iterdir()) == ["images.json"]


@pytest.mark.parametrize(
    "repo_digests",
    [[], [OTHER_REPO], [f"{REPO}@sha256:{'a' * 63}"], [f"{REPO}@sha256:{'A' * 64}"]],
    ids=["sem-digest", "so-outro-repositorio", "digest-curto", "digest-maiusculo"],
)
def test_falha_sem_digest_valido_e_nao_altera_a_allowlist(
    fake_bin: Path, images_file: Path, repo_digests: list[str]
) -> None:
    write_allowed(images_file, [OLD])

    result = run_script(fake_bin, images_file, repo_digests=repo_digests)

    assert result.returncode != 0
    assert result.stdout == ""
    assert read_allowed(images_file) == [OLD]


def test_falha_se_o_pull_falhar(fake_bin: Path, images_file: Path) -> None:
    result = run_script(fake_bin, images_file, pull_rc=1)

    assert result.returncode != 0
    assert not images_file.exists()


@pytest.mark.parametrize("tag", ["", "main", "sha-0123", "sha-0123456789ab;rm"])
def test_recusa_tag_fora_do_formato(fake_bin: Path, images_file: Path, tag: str) -> None:
    result = run_script(fake_bin, images_file, tag=tag)

    assert result.returncode != 0
    assert not (fake_bin / "calls.log").exists()


def test_ignora_entradas_invalidas_da_allowlist_anterior(fake_bin: Path, images_file: Path) -> None:
    write_allowed(images_file, ["imagem-qualquer:latest", OLD])

    run_script(fake_bin, images_file)

    assert read_allowed(images_file) == [NEW, OLD]
