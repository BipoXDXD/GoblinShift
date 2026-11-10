"""O modelo de ameaças (docs/seguranca/modelo-de-ameacas.md) só cita evidência que existe.

Formas de citação, todas conferidas aqui:
- `arquivo.py::símbolo` (teste, função, classe ou `Classe.método`), resolvido pelo `ast`;
- `` `arquivo.py` `símbolo` `trecho` `` e `` `arquivo` `trecho` `` (fora do Python), com o trecho
  exatamente uma vez no símbolo ou no arquivo (`tests/security/threat_citations.py`): editar
  código acima não reprova, trocar o código citado sim; a forma `arquivo:linha` é recusada;
- `docs/...`, que precisa existir.
"""

import ast
import re
from pathlib import Path

from tests.security.threat_citations import citation_problems, count_code_citations

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "docs" / "seguranca" / "modelo-de-ameacas.md"
_SYMBOL_REF = re.compile(r"`((?:src|tests|deploy)/[\w/.-]+\.py)::([\w.]+)`")
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


def test_documento_cita_simbolos_e_trechos() -> None:
    text = _text()

    assert len(_SYMBOL_REF.findall(text)) >= 100
    assert count_code_citations(text) >= 90


def test_todo_simbolo_citado_existe() -> None:
    missing = [
        f"{file}::{name}"
        for file, name in _SYMBOL_REF.findall(_text())
        if not (ROOT / file).is_file() or name not in _symbols(ROOT / file)
    ]

    assert missing == []


def test_todo_trecho_citado_aparece_uma_vez_no_simbolo_ou_arquivo() -> None:
    assert citation_problems(_text(), ROOT) == []


def test_todo_documento_citado_existe() -> None:
    assert [doc for doc in _DOC_REF.findall(_text()) if not (ROOT / doc).exists()] == []
