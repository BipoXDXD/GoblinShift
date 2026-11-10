"""O verificador de citações do modelo de ameaças, contra arquivos pequenos em `tmp_path`."""

from pathlib import Path

import pytest

from tests.security.threat_citations import citation_problems, count_code_citations

_MODULE = """\
import functools

LIMIT = 10
_FLAGS = ("a", "b")


def guard(value: int) -> bool:
    return value <= LIMIT


class Store:
    def open(self, key: str) -> str:
        def checked(name: str) -> str:
            if name != key:
                raise ValueError(name)
            return name

        return checked(key)

    @functools.cache
    def cached(self) -> str:
        return "a"

    def close(self) -> None:
        if self:
            return None
        return None


def twin() -> None:
    pass


def twin() -> None:  # noqa: F811
    pass
"""

_CONFIG = """\
zone api rate=20;
limit api;
limit api;
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text(_MODULE, encoding="utf-8")
    (tmp_path / "docker").mkdir()
    (tmp_path / "docker" / "edge.conf").write_text(_CONFIG, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "citation",
    [
        "`src/mod.py` `guard` `return value <= LIMIT`",
        "`src/mod.py` `Store.open` `return checked(key)`",
        "`src/mod.py` `Store.open.checked` `if name != key:`",
        "`src/mod.py` `LIMIT` `LIMIT = 10`",
        '`src/mod.py` `_FLAGS` `"b"`',
        "`docker/edge.conf` `zone api rate=20;`",
    ],
)
def test_citacao_valida_nao_tem_problema(root: Path, citation: str) -> None:
    assert citation_problems(f"Mitigação: {citation}.", root) == []


def test_trecho_no_decorador_conta_como_parte_do_simbolo(root: Path) -> None:
    assert citation_problems("`src/mod.py` `Store.cached` `@functools.cache`", root) == []


def test_trecho_que_se_repete_fora_do_simbolo_vale_uma_vez_dentro_dele(root: Path) -> None:
    # `LIMIT` aparece duas vezes no arquivo (definição e uso), mas só uma vez em `guard`.
    assert citation_problems("`src/mod.py` `guard` `LIMIT`", root) == []


@pytest.mark.parametrize(
    ("citation", "reason"),
    [
        ("`src/falta.py` `guard` `return`", "o arquivo não existe"),
        ("`src/mod.py` `Store.fechar` `return None`", "o símbolo não existe no arquivo"),
        ("`src/mod.py` `Store.open.outra` `return`", "o símbolo não existe no arquivo"),
        ("`src/mod.py` `twin` `pass`", "o símbolo está definido 2 vezes no arquivo"),
        ("`src/mod.py` `guard` `value > LIMIT`", "o trecho não aparece no símbolo"),
        ("`src/mod.py` `guard` `return checked(key)`", "o trecho não aparece no símbolo"),
        ("`src/mod.py` `Store.close` `return None`", "o trecho aparece 2 vezes no símbolo"),
        ("`src/mod.py` `1guard` `return`", "o símbolo não é um nome pontuado válido"),
        ("`docker/falta.conf` `zone`", "o arquivo não existe"),
        ("`docker/edge.conf` `limit api;`", "o trecho aparece 2 vezes no arquivo"),
        ("`docker/edge.conf` `rate=30`", "o trecho não aparece no arquivo"),
    ],
)
def test_citacao_invalida_e_recusada_com_o_motivo(root: Path, citation: str, reason: str) -> None:
    assert citation_problems(f"Mitigação: {citation}.", root) == [f"{citation}: {reason}"]


def test_citacao_python_sem_trecho_e_recusada(root: Path) -> None:
    citation = "`src/mod.py` `guard`"

    assert citation_problems(f"Mitigação: {citation}.", root) == [
        f"{citation}: citação de arquivo Python exige símbolo e trecho"
    ]


def test_citacao_de_arquivo_que_nao_e_python_nao_aceita_simbolo(root: Path) -> None:
    citation = "`docker/edge.conf` `zone` `api`"

    assert citation_problems(f"Mitigação: {citation}.", root) == [
        f"{citation}: arquivo que não é Python leva só o trecho, sem símbolo"
    ]


@pytest.mark.parametrize(
    "citation",
    ["`src/mod.py:8`", "`src/mod.py:8` `return value <= LIMIT`", "`docker/edge.conf:1`"],
)
def test_formato_antigo_com_numero_de_linha_e_recusado(root: Path, citation: str) -> None:
    file_and_line = citation.split(" ")[0]

    assert citation_problems(f"Mitigação: {citation}.", root) == [
        f"{file_and_line}: citação por número de linha; use `arquivo` `símbolo` `trecho`"
    ]


def test_todos_os_problemas_do_documento_sao_listados(root: Path) -> None:
    document = "`src/mod.py` `guard` `nada`; `src/mod.py` `Store.open` `return checked(key)`; `x`"

    assert citation_problems(document, root) == [
        "`src/mod.py` `guard` `nada`: o trecho não aparece no símbolo"
    ]


def test_contagem_considera_so_citacoes_de_codigo() -> None:
    document = (
        "`src/a.py` `f` `x`, `docker/b.conf` `y`, `src/a.py::f`, `docs/c.md`, `src/a.py:3` `x`"
    )

    assert count_code_citations(document) == 2
