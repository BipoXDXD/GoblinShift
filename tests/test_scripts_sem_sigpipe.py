"""Leitor que sai cedo num pipeline sob `pipefail` faz o passo falhar por acaso.

`head` e `grep -q` fecham o pipe assim que acham o que querem: se o comando da esquerda ainda
escreve, ele morre de SIGPIPE (141) e o `pipefail` transforma isso na falha do pipeline. Foi o que
derrubou o CI da main no `runsc --version | head -n 1`. `sed -n` e `grep` sem `-q` leem tudo.
"""

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
EARLY_EXIT_READER = re.compile(r"\|\s*(head\b|grep\s+(-\w*q\w*\b|--quiet\b))")


def pipefail_scripts() -> list[Path]:
    return sorted(p for p in SCRIPTS.glob("*.sh") if "pipefail" in p.read_text())


def early_exit_pipes(script: Path) -> list[str]:
    return [
        f"{script.name}:{number}: {line.strip()}"
        for number, line in enumerate(script.read_text().splitlines(), start=1)
        if EARLY_EXIT_READER.search(line) and "|| true" not in line
    ]


@pytest.mark.parametrize("script", pipefail_scripts(), ids=lambda p: p.name)
def test_script_com_pipefail_nao_tem_leitor_que_sai_cedo(script: Path) -> None:
    assert early_exit_pipes(script) == []


@pytest.mark.parametrize(
    "line",
    ["runsc --version | head -n 1", "docker info | grep -q runsc", "x | grep -Eq y"],
)
def test_o_detector_reconhece_o_leitor_que_sai_cedo(line: str) -> None:
    assert EARLY_EXIT_READER.search(line)
