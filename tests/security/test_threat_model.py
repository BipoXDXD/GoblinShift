"""O modelo de ameaças (docs/seguranca/modelo-de-ameacas.md) só cita evidência que existe.

Formas de citação, todas conferidas aqui:
- `arquivo.py::símbolo` (teste, função, classe, `Classe.método` ou função aninhada), resolvido por
  `resolve_symbol`, a mesma regra da forma com trecho: definido uma vez. Nome atribuído é recusado,
  porque só a forma com trecho confere o valor dele;
- `` `arquivo.py` `símbolo` `trecho` `` e `` `arquivo` `trecho` `` (fora do Python), com o trecho
  exatamente uma vez no símbolo ou no arquivo (`tests/security/threat_citations.py`): editar
  código acima não reprova, trocar o código citado sim; a forma `arquivo:linha` é recusada;
- `docs/...`, que precisa existir.
"""

import re
from pathlib import Path

import pytest

from tests.security.threat_citations import (
    Definition,
    citation_problems,
    count_code_citations,
    resolve_symbol,
)

ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = ROOT / "docs" / "seguranca" / "modelo-de-ameacas.md"
_SYMBOL_REF = re.compile(r"`((?:src|tests|deploy)/[\w/.-]+\.py)::([\w.]+)`")
_DOC_REF = re.compile(r"`(docs/[\w/.-]+)`")


def _text() -> str:
    return DOCUMENT.read_text(encoding="utf-8")


def test_documento_cita_simbolos_e_trechos() -> None:
    text = _text()

    assert len(_SYMBOL_REF.findall(text)) >= 100
    assert count_code_citations(text) >= 90


def _missing_symbols(document: str, root: Path) -> list[str]:
    """As citações `arquivo.py::símbolo` cujo arquivo não existe ou cujo símbolo não é uma definição
    única (função ou classe)."""
    return [
        f"{file}::{name}"
        for file, name in _SYMBOL_REF.findall(document)
        if not (root / file).is_file() or not _is_single_definition(root / file, name)
    ]


def _is_single_definition(path: Path, symbol: str) -> bool:
    """Nome atribuído não vale aqui: só a forma com trecho confere o valor dele."""
    return isinstance(resolve_symbol(path.read_text(encoding="utf-8"), symbol), Definition)


def test_todo_simbolo_citado_existe() -> None:
    assert _missing_symbols(_text(), ROOT) == []


def test_todo_trecho_citado_aparece_uma_vez_no_simbolo_ou_arquivo() -> None:
    assert citation_problems(_text(), ROOT) == []


def test_todo_documento_citado_existe() -> None:
    assert [doc for doc in _DOC_REF.findall(_text()) if not (ROOT / doc).exists()] == []


_MODULE = """\
MAX_UPLOAD_BYTES = 10 * 1024**2
MAX: int


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


def test_upload() -> None:
    response = guard()
    assert response is None
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


# As duas formas de citação resolvem o símbolo pela mesma regra (`resolve_symbol`).
def test_funcao_aninhada_e_encontrada(module_root: Path) -> None:
    assert _missing_symbols("`src/mod.py::guard.inner`", module_root) == []


# Nome atribuído só prova que existe: o valor é conferido pela forma com trecho.
@pytest.mark.parametrize("symbol", ["MAX_UPLOAD_BYTES", "MAX", "test_upload.response"])
def test_nome_atribuido_e_listado(module_root: Path, symbol: str) -> None:
    assert _missing_symbols(f"`src/mod.py::{symbol}`", module_root) == [f"src/mod.py::{symbol}"]


@pytest.mark.parametrize("symbol", ["inner", "twin"])
def test_simbolo_aninhado_sem_escopo_ou_definido_duas_vezes_e_listado(
    module_root: Path, symbol: str
) -> None:
    assert _missing_symbols(f"`src/mod.py::{symbol}`", module_root) == [f"src/mod.py::{symbol}"]
