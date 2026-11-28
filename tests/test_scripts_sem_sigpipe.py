"""Leitor que sai cedo num pipeline sob `pipefail` faz o passo falhar por acaso.

`head`, `grep -q` e `grep -m` fecham o pipe assim que acham o que querem: se o comando da
esquerda ainda escreve, ele morre de SIGPIPE (141) e o `pipefail` transforma isso na falha do
pipeline. Foi o que derrubou o CI da main no `runsc --version | head -n 1`. O GNU grep com a saída
em /dev/null também para no primeiro casamento, então a saída é não usar pipe: `sed -n 1p` lê a
entrada inteira, e o grep que só testa recebe o texto por here-string (`<<<"$(cmd)"`).

Vale para todo script do repositório, com ou sem `pipefail` próprio: biblioteca carregada por
`source` herda a opção de quem a carrega. `|| true` isenta a linha, porque aí a falha não conta.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EARLY_EXIT_READER = re.compile(
    r"\|\s*(head\b|grep\s+([^|]*\s)?(-[A-Za-z]*[qm]|--quiet\b|--max-count\b))"
)


def shell_scripts() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "*.sh"],  # noqa: S607 - o git do PATH
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [ROOT / name for name in listed]


def logical_lines(script: Path) -> list[tuple[int, str]]:
    """Junta a linha que termina em `|` ou `\\` com a seguinte: o leitor pode estar na de baixo."""
    joined: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for number, line in enumerate(script.read_text().splitlines(), start=1):
        current = (pending[0], f"{pending[1]} {line.strip()}") if pending else (number, line)
        if line.rstrip().endswith(("|", "\\")):
            pending = (current[0], current[1].rstrip().rstrip("\\"))
            continue
        pending = None
        joined.append(current)
    return joined


def early_exit_pipes(script: Path) -> list[str]:
    return [
        f"{script.relative_to(ROOT)}:{number}: {line.strip()}"
        for number, line in logical_lines(script)
        if EARLY_EXIT_READER.search(line) and "|| true" not in line
    ]


@pytest.mark.parametrize("script", shell_scripts(), ids=lambda p: str(p.relative_to(ROOT)))
def test_script_nao_tem_leitor_que_sai_cedo(script: Path) -> None:
    assert early_exit_pipes(script) == []


@pytest.mark.parametrize(
    "line",
    [
        "runsc --version | head -n 1",
        "x | head -c 28",
        "docker info | grep -q runsc",
        "x | grep -Eq y",
        "x | grep -E -q y",
        "x | grep -qx api ||",
        "x | grep --quiet y",
        "x | grep -m1 y",
        "x | grep --max-count=1 y",
    ],
)
def test_o_detector_reconhece_o_leitor_que_sai_cedo(line: str) -> None:
    assert EARLY_EXIT_READER.search(line)


@pytest.mark.parametrize(
    "line",
    [
        "runsc --version | sed -n 1p",
        'grep -q \'"runsc"\' <<<"$(docker info)"',
        "x | grep -o '/assets/[a-z]+' | sort",
        "x | tr -dc 'A-Za-z0-9' | cut -c1-28",
    ],
)
def test_o_detector_aceita_quem_le_a_entrada_inteira(line: str) -> None:
    assert not EARLY_EXIT_READER.search(line)


def test_leitor_na_linha_de_baixo_do_pipe_tambem_conta(tmp_path: Path) -> None:
    script = tmp_path / "x.sh"
    script.write_text("runsc --version |\n  head -n 1\n")

    assert [line for _, line in logical_lines(script) if EARLY_EXIT_READER.search(line)]
