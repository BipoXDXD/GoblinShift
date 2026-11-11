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
    """As citações `arquivo.py::símbolo` cujo símbolo não é uma definição única (função ou classe),
    no formato "<citação>: <motivo>"."""
    problems = [
        (f"{file}::{name}", _symbol_problem(root / file, name))
        for file, name in _SYMBOL_REF.findall(document)
    ]
    return [f"{citation}: {reason}" for citation, reason in problems if reason is not None]


def _symbol_problem(path: Path, symbol: str) -> str | None:
    """Nome atribuído não vale aqui: só a forma com trecho confere o valor dele."""
    if not path.is_file():
        return "o arquivo não existe"
    node = resolve_symbol(path.read_text(encoding="utf-8"), symbol)
    if isinstance(node, str):
        return node
    if not isinstance(node, Definition):
        return (
            "existe, mas é nome atribuído, não definição; "
            "use a forma com trecho (`arquivo.py` `símbolo` `trecho`)"
        )
    return None


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


@pytest.mark.parametrize(
    ("citation", "reason"),
    [
        ("src/mod.py::Store.close", "o símbolo não existe no arquivo"),
        ("src/falta.py::guard", "o arquivo não existe"),
    ],
)
def test_simbolo_ou_arquivo_inexistente_e_listado_com_motivo(
    module_root: Path, citation: str, reason: str
) -> None:
    assert _missing_symbols(f"`{citation}`", module_root) == [f"{citation}: {reason}"]


# As duas formas de citação resolvem o símbolo pela mesma regra (`resolve_symbol`).
def test_funcao_aninhada_e_encontrada(module_root: Path) -> None:
    assert _missing_symbols("`src/mod.py::guard.inner`", module_root) == []


# Nome atribuído só prova que existe: o valor é conferido pela forma com trecho. O motivo diz que
# ele existe, para quem esbarra não achar que o símbolo sumiu.
@pytest.mark.parametrize("symbol", ["MAX_UPLOAD_BYTES", "MAX", "test_upload.response"])
def test_nome_atribuido_e_listado_com_motivo_proprio(module_root: Path, symbol: str) -> None:
    assert _missing_symbols(f"`src/mod.py::{symbol}`", module_root) == [
        f"src/mod.py::{symbol}: existe, mas é nome atribuído, não definição; "
        "use a forma com trecho (`arquivo.py` `símbolo` `trecho`)"
    ]


@pytest.mark.parametrize(
    ("symbol", "reason"),
    [
        ("inner", "o símbolo não existe no arquivo"),
        ("twin", "o símbolo está definido 2 vezes no arquivo"),
    ],
)
def test_simbolo_aninhado_sem_escopo_ou_definido_duas_vezes_e_listado_com_motivo(
    module_root: Path, symbol: str, reason: str
) -> None:
    assert _missing_symbols(f"`src/mod.py::{symbol}`", module_root) == [
        f"src/mod.py::{symbol}: {reason}"
    ]
