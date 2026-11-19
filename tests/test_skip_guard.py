"""`--fail-on-skip` reprova a suíte com teste pulado ou em xfail, e só quando ligado."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PASSING = "def test_passa():\n    assert True\n"
SKIPPED = "import pytest\n\ndef test_pulado():\n    pytest.skip('sem a ferramenta')\n"
SKIPPED_BY_MARKER = (
    "import pytest\n\n@pytest.mark.skipif(True, reason='sem a ferramenta')\n"
    "def test_pulado():\n    assert True\n"
)
SKIPPED_BY_FIXTURE = (
    "import pytest\n\n@pytest.fixture\ndef ferramenta():\n    pytest.skip('sem a ferramenta')\n\n"
    "def test_pulado(ferramenta):\n    assert True\n"
)
XFAILED = (
    "import pytest\n\n@pytest.mark.xfail(strict=True)\n"
    "def test_falha_esperada():\n    assert False\n"
)
MODULE_SKIPPED = (
    "import pytest\n\npytest.skip('módulo sem a ferramenta', allow_module_level=True)\n\n"
    "def test_nunca_roda():\n    assert True\n"
)
DESELECTED = "import pytest\n\n@pytest.mark.lento\ndef test_lento():\n    assert True\n"


def run_suite(tmp_path: Path, source: str, *options: str) -> subprocess.CompletedProcess[str]:
    """Roda uma suíte isolada, sem a configuração do repositório, com o guard carregado."""
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    lento: deselecionado\n")
    (tmp_path / "test_amostra.py").write_text(source)
    environment = {**os.environ, "PYTHONPATH": str(ROOT)}
    command = [sys.executable, "-m", "pytest", "-p", "tests.skip_guard", "-p", "no:cacheprovider"]
    return subprocess.run(  # noqa: S603 (só o Python do teste e opções fixas)
        [*command, *options],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "source",
    [SKIPPED, SKIPPED_BY_MARKER, SKIPPED_BY_FIXTURE, XFAILED],
    ids=["skip", "skipif-setup", "fixture-setup", "xfail"],
)
def test_com_a_opcao_teste_que_nao_rodou_reprova_a_suite(tmp_path: Path, source: str) -> None:
    result = run_suite(tmp_path, source, "--fail-on-skip")

    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout
    assert "--fail-on-skip: 1 teste(s) pulado(s) ou em xfail" in result.stdout


def test_com_a_opcao_suite_sem_skip_passa(tmp_path: Path) -> None:
    result = run_suite(tmp_path, PASSING, "--fail-on-skip")

    assert result.returncode == pytest.ExitCode.OK, result.stdout


def test_teste_deselecionado_por_marcador_nao_conta_como_pulado(tmp_path: Path) -> None:
    result = run_suite(tmp_path, PASSING + DESELECTED, "--fail-on-skip", "-m", "not lento")

    assert result.returncode == pytest.ExitCode.OK, result.stdout
    assert "1 deselected" in result.stdout


def test_sem_a_opcao_skip_nao_muda_o_resultado(tmp_path: Path) -> None:
    result = run_suite(tmp_path, SKIPPED)

    assert result.returncode == pytest.ExitCode.OK, result.stdout


def test_com_a_opcao_skip_no_nivel_do_modulo_reprova_a_suite(tmp_path: Path) -> None:
    result = run_suite(tmp_path, MODULE_SKIPPED, "--fail-on-skip")

    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout


def test_com_a_opcao_e_sem_o_terminal_o_skip_ainda_reprova(tmp_path: Path) -> None:
    # Sem o terminalreporter não há estatística para ler: a contagem vem dos relatórios.
    result = run_suite(tmp_path, SKIPPED, "--fail-on-skip", "-p", "no:terminal")

    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout
