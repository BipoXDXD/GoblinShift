"""O modelo de ameaças (docs/seguranca/modelo-de-ameacas.md) só cita evidência que existe.

Cada `tests/...py::teste` precisa ser um teste definido no arquivo, e cada `arquivo:linha` precisa
apontar uma linha que existe: um teste renomeado ou um arquivo encolhido reprova aqui, em vez de
deixar o documento afirmando uma garantia que sumiu.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "docs" / "seguranca" / "modelo-de-ameacas.md"
_TEST_REF = re.compile(r"`(tests/[\w/]+\.py)::(\w+)`")
_LINE_REF = re.compile(r"`((?:src|deploy|docker)/[\w/.]+):(\d+)`")


def _text() -> str:
    return DOCUMENT.read_text(encoding="utf-8")


def _defines_test(path: Path, name: str) -> bool:
    source = path.read_text(encoding="utf-8")
    return re.search(rf"^(?:async )?def {name}\(", source, re.MULTILINE) is not None


def test_documento_cita_testes_e_linhas() -> None:
    text = _text()

    assert len(_TEST_REF.findall(text)) >= 50
    assert len(_LINE_REF.findall(text)) >= 50


def test_todo_teste_citado_existe() -> None:
    missing = [
        f"{file}::{name}"
        for file, name in _TEST_REF.findall(_text())
        if not (ROOT / file).is_file() or not _defines_test(ROOT / file, name)
    ]

    assert missing == []


def test_toda_linha_citada_existe_no_arquivo() -> None:
    invalid = []
    for file, line in _LINE_REF.findall(_text()):
        path = ROOT / file
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        if not 1 <= int(line) <= len(lines) or not lines[int(line) - 1].strip():
            invalid.append(f"{file}:{line}")

    assert invalid == []
