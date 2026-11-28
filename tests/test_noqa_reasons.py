"""Toda supressão do N818 diz por que a exceção não termina em Error (#442)."""

from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
BARE = "noqa: N818"
WITH_REASON = "noqa: N818 -"


def python_lines(root: Path) -> Iterator[tuple[Path, int, str]]:
    for path in sorted(root.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            yield path, number, line


def test_todo_noqa_n818_tem_motivo() -> None:
    bare = [
        f"{path.relative_to(SRC)}:{number}"
        for path, number, line in python_lines(SRC)
        if BARE in line and WITH_REASON not in line
    ]

    assert bare == []


def test_a_varredura_enxerga_os_noqa_com_motivo() -> None:
    # Sem isto, um SRC errado (vazio) faria o teste acima passar sem olhar nada.
    assert any(WITH_REASON in line for _, _, line in python_lines(SRC))
