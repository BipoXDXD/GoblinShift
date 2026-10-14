"""Valores lidos do banco restaurado são dados, nunca código (ADR 9xx).

O dump vem de fora do runner da restauração: quem controla a VPS tem a chave que escreve no
bucket e a chave pública age, então consegue enviar um dump forjado. O pg_restore roda esse
dump no PostgreSQL efêmero, com um papel sem superusuário, e o que o psql devolve volta para o
bash. A avaliação aritmética do bash (`((x > 0))`) executa substituições como `a[$(comando)]`,
e no runner estão a chave privada age e a chave de leitura: todo valor do banco passa por
`require_count` ou `require_revision` antes de ser usado.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "deploy" / "backup" / "lib.sh"
RESTORE_SCRIPT = ROOT / "deploy" / "backup" / "pg-restore-check.sh"


def run_lib(function: str, value: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "$1"; {function} rótulo "$2"', "_", LIB, value],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("value", ["0", "7", "1234567890"])
def test_contagem_decimal_e_aceita(value: str) -> None:
    result = run_lib("require_count", value)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "value",
    ["", "-1", "1.5", "0x10", " 3", "3\n4", "nada", "a[$(touch {marker})]", "$(touch {marker})"],
)
def test_contagem_que_nao_e_decimal_falha_sem_executar_nada(value: str, tmp_path: Path) -> None:
    marker = tmp_path / "executado"

    result = run_lib("require_count", value.format(marker=marker))

    assert result.returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize("value", ["0004", "0005", "abc_123"])
def test_revisao_com_formato_do_alembic_e_aceita(value: str) -> None:
    result = run_lib("require_revision", value)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["", "0004 0005", "*", "../x", "0004\n", "$(id)"])
def test_revisao_fora_do_formato_falha(value: str) -> None:
    result = run_lib("require_revision", value)

    assert result.returncode != 0


@pytest.mark.parametrize("variable", ["tables", "accounts", "runs"])
def test_restauracao_valida_cada_contagem_antes_da_aritmetica(variable: str) -> None:
    lines = RESTORE_SCRIPT.read_text().splitlines()
    validation = f'require_count {variable} "${variable}"'
    first_arithmetic = next(i for i, line in enumerate(lines) if f"(({variable} " in line)

    assert validation in lines[:first_arithmetic]


def test_restauracao_valida_a_revisao_antes_de_usar() -> None:
    script = RESTORE_SCRIPT.read_text()

    assert script.index('require_revision revision "$revision"') < script.index(' $revision "*')
