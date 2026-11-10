"""O modelo de ameaças (docs/seguranca/modelo-de-ameacas.md) só cita evidência que existe.

Três formas de citação, todas conferidas aqui:
- `arquivo.py::símbolo` (teste, função, classe ou `Classe.método`), resolvido pelo `ast`;
- `arquivo:linha` seguido do trecho literal `` `texto` ``, que precisa estar naquela linha: um
  deslocamento de linhas ou uma troca de código reprova em vez de deixar a citação mentindo;
- `docs/...`, que precisa existir.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "docs" / "seguranca" / "modelo-de-ameacas.md"
_SYMBOL_REF = re.compile(r"`((?:src|tests|deploy)/[\w/.-]+\.py)::([\w.]+)`")
_LINE_REF = re.compile(r"`((?:src|deploy|docker)/[\w/.-]+):(\d+)`(?: `([^`]+)`)?")
_DOC_REF = re.compile(r"`(docs/[\w/.-]+)`")


def _text() -> str:
    return DOCUMENT.read_text(encoding="utf-8")


def _symbols(path: Path) -> set[str]:
    """Nomes definidos no módulo, com `Classe.método` para os métodos."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        if isinstance(node, ast.ClassDef):
            names.update(
                f"{node.name}.{item.name}"
                for item in node.body
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            )
    return names


def test_documento_cita_simbolos_e_linhas() -> None:
    text = _text()

    assert len(_SYMBOL_REF.findall(text)) >= 100
    assert len(_LINE_REF.findall(text)) >= 40


def test_todo_simbolo_citado_existe() -> None:
    missing = [
        f"{file}::{name}"
        for file, name in _SYMBOL_REF.findall(_text())
        if not (ROOT / file).is_file() or name not in _symbols(ROOT / file)
    ]

    assert missing == []


def test_toda_linha_citada_traz_o_trecho_que_esta_nela() -> None:
    invalid = []
    for file, line, excerpt in _LINE_REF.findall(_text()):
        path = ROOT / file
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        number = int(line)
        if not excerpt or not 1 <= number <= len(lines) or excerpt not in lines[number - 1]:
            invalid.append(f"{file}:{line} `{excerpt}`")

    assert invalid == []


def test_todo_documento_citado_existe() -> None:
    assert [doc for doc in _DOC_REF.findall(_text()) if not (ROOT / doc).exists()] == []
