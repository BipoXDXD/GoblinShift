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

import pytest

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


def _missing_symbols(document: str, root: Path) -> list[str]:
    """As citações `arquivo.py::símbolo` cujo arquivo ou símbolo não existe."""
    return [
        f"{file}::{name}"
        for file, name in _SYMBOL_REF.findall(document)
        if not (root / file).is_file() or name not in _symbols(root / file)
    ]


def test_todo_simbolo_citado_existe() -> None:
    assert _missing_symbols(_text(), ROOT) == []


def test_todo_trecho_citado_aparece_uma_vez_no_simbolo_ou_arquivo() -> None:
    assert citation_problems(_text(), ROOT) == []


def test_todo_documento_citado_existe() -> None:
    assert [doc for doc in _DOC_REF.findall(_text()) if not (ROOT / doc).exists()] == []


_MODULE = """\
LIMIT = 10


def guard() -> None:
    def inner() -> None:
        pass


class Store:
    async def open(self) -> None:
        pass


def twin() -> None:
    pass


def twin() -> None:  # noqa: F811
    pass
"""


@pytest.fixture
def module_root(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text(_MODULE, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("symbol", ["guard", "Store", "Store.open"])
def test_simbolo_citado_por_dois_pontos_e_encontrado(module_root: Path, symbol: str) -> None:
    assert _missing_symbols(f"`src/mod.py::{symbol}`", module_root) == []


@pytest.mark.parametrize("citation", ["src/mod.py::Store.close", "src/falta.py::guard"])
def test_simbolo_ou_arquivo_inexistente_e_listado(module_root: Path, citation: str) -> None:
    assert _missing_symbols(f"`{citation}`", module_root) == [citation]
