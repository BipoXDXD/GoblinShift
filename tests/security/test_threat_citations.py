"""O verificador de citações do modelo de ameaças, contra arquivos pequenos em `tmp_path`."""

from pathlib import Path

import pytest

from tests.security.threat_citations import citation_problems, count_code_citations

_MODULE = """\
import contextlib
import functools

LIMIT = 10
Number = int | float
_FLAGS = ("a", "b")
_PAIRS = (
    ("a", 1),
    ("b", 2),
)
_POLICY = {
    "csp": "none",
    "corp": "same-origin",
}
_TABLE = Table(
    "t",
    metadata,
    # comentário dentro da chamada
    Column("a"),
    Key(["a", "b"], ["c.a", "c.b"], on="x"),
)

try:
    import tomllib
except ImportError:
    TRY_LIMIT = 1

if LIMIT:
    IF_LIMIT = 2

with contextlib.suppress(ValueError):
    WITH_LIMIT = 3


async def fetch() -> int:
    return LIMIT


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


_WHOLE_STATEMENT = "numa atribuição de uma linha, o trecho é a instrução inteira"
_ITEM = "numa atribuição de várias linhas, o trecho é um item do valor atribuído"
_REPEATED_KEY = "a chave do item citado se repete no mesmo valor"
_IDENTIFIER = "o trecho de uma atribuição é só um identificador"
_UNPACKING = "o valor do item citado tem `*` ou `**`, que pode repetir a chave"

# As sabotagens da revisão do PR #256: cada valor mudou, mas o trecho antigo continuava dentro dele.
_SABOTAGED = """\
RETURN_TO_PATTERN = r".*"
MAX_TRANSFERS_PER_ACCOUNT = 200
MAX_BODY_BYTES = 10240000
_DIR_MODE = 0o7007
"""


# As sabotagens A1, A2 e A3 da re-revisão do PR #256: a linha citada não muda, mas outro item vence
# ou o item citado sai do valor atribuído.
_SABOTAGED_ITEMS = """\
_COMMON_HEADERS = (
    ("cache-control", "public, max-age=86400"),
    ("cache-control", "no-store"),
)
DOWNLOAD_PROTECTION_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Content-Security-Policy": "default-src *",
}
run_input = Table(
    "run_input",
    metadata,
    *() and ForeignKeyConstraint(
        ["owner_id", "upload_id"], ["upload.owner_id", "upload.id"], ondelete="RESTRICT"
    ),
)
"""


# As sabotagens B1, B11, B2, B3 e B4b da re-revisão (rodada 3): outra caixa na chave, irmão
# desempacotado, e o item citado levado para dentro de um valor de keyword ou de dict.
_SABOTAGED_KEYS = """\
_HEADERS_CASE = (
    ("Cache-Control", "public, max-age=86400"),
    ("cache-control", "no-store"),
)
_HEADERS_STAR = (
    *(("cache-control", "public, max-age=86400"),),
    ("cache-control", "no-store"),
)
DOWNLOAD = {
    "Content-Security-Policy": "default-src 'none'; sandbox",
    **{"Content-Security-Policy": "default-src *"},
}
audit = Table(
    "audit",
    *extra_columns,
    Column("user_id", Uuid),
)
run_input = Table(
    "run_input",
    metadata,
    info={
        "fk": ForeignKeyConstraint(["owner_id", "upload_id"], ["upload.owner_id", "upload.id"]),
    },
)
idempotency_record = Table(
    "idempotency_record",
    metadata,
    info={"pk": (PrimaryKeyConstraint("owner_id", "operation", "idem_key"),)},
)
only_keyword = Table(
    "only_keyword",
    metadata,
    info=ForeignKeyConstraint(["owner_id"], ["app_user.id"]),
)
ONLY_DICT = {
    "pk": (PrimaryKeyConstraint("owner_id", "run_id"),),
}
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
        '`src/mod.py` `_FLAGS` `_FLAGS = ("a", "b")`',
        '`src/mod.py` `_PAIRS` `("b", 2),`',
        '`src/mod.py` `_PAIRS` `("b", 2)`',
        '`src/mod.py` `_POLICY` `"csp": "none",`',
        '`src/mod.py` `_TABLE` `Column("a"),`',
        '`src/mod.py` `_TABLE` `["c.a", "c.b"]`',
        '`src/mod.py` `_TABLE` `on="x"`',
        "`src/mod.py` `TRY_LIMIT` `TRY_LIMIT = 1`",
        "`src/mod.py` `IF_LIMIT` `IF_LIMIT = 2`",
        "`src/mod.py` `WITH_LIMIT` `WITH_LIMIT = 3`",
        "`src/mod.py` `fetch` `return LIMIT`",
        "`docker/edge.conf` `zone api rate=20;`",
    ],
)
def test_citacao_valida_nao_tem_problema(root: Path, citation: str) -> None:
    assert citation_problems(f"Mitigação: {citation}.", root) == []


def test_barra_vertical_escapada_para_a_tabela_vale_como_barra(root: Path) -> None:
    # Dentro de célula de tabela GFM o `|` do código é escrito `\|`, também em code span.
    assert citation_problems("| x | `src/mod.py` `Number` `Number = int \\| float` |", root) == []


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
        ("`src/mod.py` `checked` `return name`", "o símbolo não existe no arquivo"),
        ("`src/mod.py` `Store.checked` `return name`", "o símbolo não existe no arquivo"),
        ("`src/mod.py` `guard` `guard`", "o trecho é só o nome do símbolo"),
        ("`src/mod.py` `LIMIT` `LIMIT`", "o trecho é só o nome do símbolo"),
        ("`src/mod.py` `LIMIT` `= 10`", _WHOLE_STATEMENT),
        ('`src/mod.py` `_FLAGS` `"b"`', _IDENTIFIER),
        ('`src/mod.py` `_FLAGS` `("a", "b")`', _WHOLE_STATEMENT),
        ('`src/mod.py` `_PAIRS` `("b", 2`', _ITEM),
        ("`src/mod.py` `_PAIRS` `_PAIRS = (`", _IDENTIFIER),
        ("`src/mod.py` `_TABLE` `# comentário dentro da chamada`", _ITEM),
        ('`src/mod.py` `_TABLE` `["a", "b"], ["c.a", "c.b"], on="x"`', _ITEM),
        ("`src/mod.py` `_TABLE` `metadata,`", _IDENTIFIER),
        ('`src/mod.py` `_TABLE` `"t",`', _IDENTIFIER),
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


@pytest.mark.parametrize(
    ("citation", "reason"),
    [
        ("`src/sab.py` `RETURN_TO_PATTERN` `RETURN_TO_PATTERN`", "o trecho é só o nome do símbolo"),
        (
            '`src/sab.py` `RETURN_TO_PATTERN` `RETURN_TO_PATTERN = re.compile(r"/(?!/)[a-z]*")`',
            _WHOLE_STATEMENT,
        ),
        ("`src/sab.py` `MAX_TRANSFERS_PER_ACCOUNT` `= 2`", _WHOLE_STATEMENT),
        (
            "`src/sab.py` `MAX_TRANSFERS_PER_ACCOUNT` `MAX_TRANSFERS_PER_ACCOUNT = 2`",
            _WHOLE_STATEMENT,
        ),
        ("`src/sab.py` `MAX_BODY_BYTES` `MAX_BODY_BYTES = 1024`", _WHOLE_STATEMENT),
        ("`src/sab.py` `_DIR_MODE` `_DIR_MODE = 0o700`", _WHOLE_STATEMENT),
    ],
)
def test_constante_sabotada_nao_casa_com_a_citacao_original(
    tmp_path: Path, citation: str, reason: str
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "sab.py").write_text(_SABOTAGED, encoding="utf-8")

    assert citation_problems(citation, tmp_path) == [f"{citation}: {reason}"]


@pytest.mark.parametrize("separator", ["\x0c", "\x1c", "\u2028"])
def test_arquivo_com_quebra_que_o_ast_nao_conta_e_recusado(tmp_path: Path, separator: str) -> None:
    # `str.splitlines` quebra nesses caracteres e o `ast` não: o recorte do símbolo sairia torto.
    (tmp_path / "src").mkdir()
    module = f"# a{separator}b\nLIMIT = 10\n"
    (tmp_path / "src" / "mod.py").write_text(module, encoding="utf-8")
    citation = "`src/mod.py` `LIMIT` `LIMIT = 10`"

    assert citation_problems(citation, tmp_path) == [
        f"{citation}: o arquivo tem quebras de linha que o ast não conta"
    ]


@pytest.mark.parametrize(
    ("citation", "reason"),
    [
        ('`src/sab.py` `_COMMON_HEADERS` `("cache-control", "no-store"),`', _REPEATED_KEY),
        (
            "`src/sab.py` `DOWNLOAD_PROTECTION_HEADERS` "
            '`"Content-Security-Policy": "default-src \'none\'; sandbox",`',
            _REPEATED_KEY,
        ),
        ('`src/sab.py` `run_input` `["upload.owner_id", "upload.id"]`', _ITEM),
    ],
)
def test_item_sabotado_em_atribuicao_de_varias_linhas_e_recusado(
    tmp_path: Path, citation: str, reason: str
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "sab.py").write_text(_SABOTAGED_ITEMS, encoding="utf-8")

    assert citation_problems(citation, tmp_path) == [f"{citation}: {reason}"]


@pytest.mark.parametrize(
    ("citation", "reason"),
    [
        ('`src/sab.py` `_HEADERS_CASE` `("cache-control", "no-store"),`', _REPEATED_KEY),
        ('`src/sab.py` `_HEADERS_STAR` `("cache-control", "no-store"),`', _UNPACKING),
        (
            '`src/sab.py` `DOWNLOAD` `"Content-Security-Policy": "default-src \'none\'; sandbox",`',
            _UNPACKING,
        ),
        ('`src/sab.py` `audit` `Column("user_id", Uuid),`', _UNPACKING),
        ('`src/sab.py` `run_input` `["upload.owner_id", "upload.id"]`', _ITEM),
        (
            "`src/sab.py` `idempotency_record` "
            '`PrimaryKeyConstraint("owner_id", "operation", "idem_key"),`',
            _ITEM,
        ),
        ('`src/sab.py` `only_keyword` `["app_user.id"]`', _ITEM),
        ('`src/sab.py` `ONLY_DICT` `PrimaryKeyConstraint("owner_id", "run_id"),`', _ITEM),
    ],
)
def test_chave_com_outra_caixa_desempacotamento_ou_item_em_valor_nomeado_sao_recusados(
    tmp_path: Path, citation: str, reason: str
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "sab.py").write_text(_SABOTAGED_KEYS, encoding="utf-8")

    assert citation_problems(citation, tmp_path) == [f"{citation}: {reason}"]
