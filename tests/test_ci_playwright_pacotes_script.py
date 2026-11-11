"""scripts/ci-playwright-pacotes.sh: os pacotes do apt que faltam ao Playwright, sem rodar o apt.

Um `npx` falso imprime o que o `playwright install-deps --dry-run` da 1.64.1 (a do lockfile)
imprime: `reportMissingDependenciesLinux` simula `apt-get install -s` e escreve
`Missing system dependencies (N):` com uma linha `  pacote` por falta e sai com 1, ou
`All system dependencies are installed.` e sai com 0.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci-playwright-pacotes.sh"
SCRIPT_TIMEOUT_S = 10
FALTANDO = "Missing system dependencies (3):\n  fonts-liberation\n  libasound2t64\n  libnss3\n"
TUDO_INSTALADO = "All system dependencies are installed.\n"
FAKE_NPX = """#!/usr/bin/env bash
echo "npx $*" >> "$FAKE_LOG"
printf '%s' "$FAKE_SAIDA"
exit "$FAKE_STATUS"
"""


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    npx = directory / "npx"
    npx.write_text(FAKE_NPX)
    npx.chmod(0o755)
    return directory


def pacotes(
    bin_dir: Path, *navegadores: str, saida: str, status: int
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LOG": str(bin_dir / "chamadas"),
        "FAKE_SAIDA": saida,
        "FAKE_STATUS": str(status),
    }
    return subprocess.run(  # noqa: S603 - script do repositório
        ["bash", str(SCRIPT), *navegadores],  # noqa: S607 - bash do PATH
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
    )


def test_lista_os_pacotes_que_faltam_um_por_linha(bin_dir: Path) -> None:
    result = pacotes(bin_dir, "chromium", saida=FALTANDO, status=1)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["fonts-liberation", "libasound2t64", "libnss3"]


def test_tudo_instalado_lista_nada_e_sai_com_zero(bin_dir: Path) -> None:
    result = pacotes(bin_dir, "chromium", saida=TUDO_INSTALADO, status=0)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_pergunta_ao_playwright_so_em_dry_run_pelos_navegadores_pedidos(bin_dir: Path) -> None:
    pacotes(bin_dir, "chromium", "firefox", saida=TUDO_INSTALADO, status=0)

    chamadas = (bin_dir / "chamadas").read_text().splitlines()
    assert chamadas == ["npx playwright install-deps --dry-run chromium firefox"]


def test_sem_navegador_falha_com_uso(bin_dir: Path) -> None:
    result = pacotes(bin_dir, saida=TUDO_INSTALADO, status=0)

    assert result.returncode == 2
    assert "uso:" in result.stderr


@pytest.mark.parametrize(
    ("saida", "status"),
    [
        # Erro de verdade do Playwright (o `apt-get install -s` falhou): sai com 1 sem a lista.
        ("Error: 'apt-get install -s' exited with code 100:\nE: Unable to locate package x\n", 1),
        # Faltas com status 0, ou tudo instalado com status 1: não é a saída conhecida.
        (FALTANDO, 0),
        (TUDO_INSTALADO, 1),
        # O formato antigo (até a 1.5x), que imprimia o comando do apt.
        ('sudo -- sh -c "apt-get update&& apt-get install -y --no-install-recommends a"\n', 0),
        # Contagem que não bate com as linhas.
        ("Missing system dependencies (2):\n  libnss3\n", 1),
        # Linha que não é `  pacote` depois do cabeçalho.
        ("Missing system dependencies (1):\n  libnss3 extra\n", 1),
        ("", 0),
        ("", 3),
    ],
)
def test_saida_desconhecida_falha_sem_listar_nada(bin_dir: Path, saida: str, status: int) -> None:
    result = pacotes(bin_dir, "chromium", saida=saida, status=status)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "install-deps --dry-run" in result.stderr
