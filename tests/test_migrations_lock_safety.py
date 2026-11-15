"""Migration não segura lock que trava a API sem declarar por quê (ADR 057).

Vale para todas as migrations, inclusive as anteriores ao check, marcadas com o motivo de cada
operação. O `downgrade()` fica de fora, como no check de contract. As regras estão em
`tests/migration_lock_check.py` (chamadas) e `tests/migration_sql.py` (SQL literal).

Os imports entram no fim de cada fonte (`IMPORTS`), para as linhas esperadas contarem a partir de
`def upgrade()`: o check só confia em `op` e `sa` importados.
"""

import time
from pathlib import Path

import pytest

from tests.migration_lock_check import (
    REVISION_FILE,
    SAFE_OPERATIONS,
    checked_revisions,
    concurrent_in_transaction_lines,
    concurrent_index_without_cleanup_lines,
    unmarked_blocking_lines,
)
from tests.migration_sql import SAFE_STATEMENTS

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
# Folga larga para a máquina do CI: o caso linear leva milissegundos.
MAX_ANALYSIS_S = 2.0
IMPORTS = (
    "\n\nfrom alembic import op\n"
    "import sqlalchemy as sa\n"
    "from sqlalchemy.dialects import postgresql\n"
)


def _upgrade(*statements: str) -> str:
    """`def upgrade()` com uma instrução por linha, a partir da linha 2, e os imports no fim."""
    body = "".join(f"    {statement}\n" for statement in statements)
    return f"def upgrade() -> None:\n{body}{IMPORTS}"


def test_migrations_so_bloqueiam_com_a_marca_lock_ok() -> None:
    problems = {}
    for name in checked_revisions([path.name for path in VERSIONS.glob("*.py")]):
        source = (VERSIONS / name).read_text()
        blocking = unmarked_blocking_lines(source)
        concurrent = concurrent_in_transaction_lines(source)
        without_cleanup = concurrent_index_without_cleanup_lines(source)
        if blocking or concurrent or without_cleanup:
            problems[name] = {
                "sem lock-ok": blocking,
                "CONCURRENTLY dentro de transação": concurrent,
                "índice CONCURRENTLY sem DROP IF EXISTS antes": without_cleanup,
            }

    assert problems == {}


def test_todo_arquivo_de_migration_comeca_pelo_numero_da_revisao() -> None:
    # Um nome fora do padrão escaparia do check em silêncio.
    names = [path.name for path in VERSIONS.glob("*.py")]

    assert [name for name in names if not REVISION_FILE.match(name)] == []


def test_check_cobre_todas_as_migrations() -> None:
    # As antigas também: as que travam levam `# lock-ok:` com o motivo de cada operação.
    names = ["0001_a.py", "0018_b.py", "0019_c.py", "README.md", "__init__.py"]

    assert checked_revisions(names) == ["0001_a.py", "0018_b.py", "0019_c.py"]


# Uma forma aceita por entrada de SAFE_OPERATIONS: tirar a entrada reprova o caso.
SAFE_OPERATION_CASES = [
    ("create_table", 'op.create_table("share", sa.Column("id", sa.BigInteger(), nullable=False))'),
    ("create_index", 'op.create_index("ix", "run", ["a"], postgresql_concurrently=True)'),
    ("add_column", 'op.add_column("run", sa.Column("note", sa.Text()))'),
    ("execute", 'op.execute("CREATE INDEX CONCURRENTLY ix ON run (a)")'),
    ("f", 'nome = op.f("ix_run_a")'),
    ("get_context", "contexto = op.get_context()"),
]


@pytest.mark.parametrize(
    ("name", "operation"), SAFE_OPERATION_CASES, ids=[name for name, _ in SAFE_OPERATION_CASES]
)
def test_cada_operacao_da_allowlist_passa_sem_marca(name: str, operation: str) -> None:
    assert unmarked_blocking_lines(_upgrade(operation)) == []


def test_allowlist_de_operacoes_tem_um_caso_por_entrada() -> None:
    assert {name for name, _ in SAFE_OPERATION_CASES} == set(SAFE_OPERATIONS)


# Uma instrução aceita por nó de SAFE_STATEMENTS.
SAFE_SQL_BY_NODE = [
    ("CreateStmt", "CREATE TABLE share (id bigint PRIMARY KEY, run_id uuid REFERENCES run (id))"),
    ("IndexStmt", "CREATE UNIQUE INDEX CONCURRENTLY ix ON run (a) WHERE a IS NOT NULL"),
    ("AlterTableStmt", "ALTER TABLE run ADD COLUMN note text, ADD COLUMN size bigint NULL"),
]


@pytest.mark.parametrize(("node", "sql"), SAFE_SQL_BY_NODE, ids=[n for n, _ in SAFE_SQL_BY_NODE])
def test_cada_no_da_allowlist_de_sql_passa_sem_marca(node: str, sql: str) -> None:
    assert unmarked_blocking_lines(_upgrade(f'op.execute("{sql}")')) == []
    assert unmarked_blocking_lines(_upgrade(f'op.execute(sa.text("{sql}"))')) == []


def test_allowlist_de_sql_tem_um_caso_por_no() -> None:
    assert {node for node, _ in SAFE_SQL_BY_NODE} == {node.__name__ for node in SAFE_STATEMENTS}


@pytest.mark.parametrize(
    "operation",
    [
        'op.rename_table("run", "job")',
        'op.drop_column("run", "note")',
        'op.drop_index("ix", table_name="run")',
        'op.alter_column("run", "note", nullable=True)',
        'op.create_check_constraint("ck", "run", "a > 0", postgresql_not_valid=True)',
        'op.create_foreign_key("fk", "run", "app_user", ["owner_id"], ["id"])',
        'op.drop_constraint("ck", "run", type_="check")',
        'op.bulk_insert(tabela, [{"a": 1}])',
        'op.get_bind().execute(sa.text("SELECT 1"))',
        "op.execute(\"COMMENT ON TABLE run IS 'x'\")",
        'op.execute("UPDATE run SET a = 1")',
        'op.execute("VACUUM run")',
    ],
    ids=[
        "operacao-desconhecida",
        "drop-column",
        "drop-index",
        "alter-column",
        "check-not-valid",
        "foreign-key",
        "drop-constraint",
        "bulk-insert",
        "get-bind",
        "no-de-sql-desconhecido",
        "dml",
        "vacuum",
    ],
)
def test_operacao_ou_no_fora_da_allowlist_pede_a_marca(operation: str) -> None:
    # Mesmo o que não trava nada pede a marca: um falso positivo custa um comentário.
    assert unmarked_blocking_lines(_upgrade(operation)) == [2]


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE INDEX ix ON run (a)",
        "ALTER TABLE run ADD COLUMN c int DEFAULT 0",
        "ALTER TABLE run ADD COLUMN c int NOT NULL",
        "ALTER TABLE run ADD COLUMN c int UNIQUE",
        "ALTER TABLE run ADD COLUMN c uuid REFERENCES app_user (id)",
        "ALTER TABLE run ADD COLUMN c int CHECK (c > 0)",
        'ALTER TABLE run ADD COLUMN c text COLLATE \\"C\\"',
        "ALTER TABLE run ADD COLUMN c bigint GENERATED ALWAYS AS IDENTITY",
        "ALTER TABLE run ADD COLUMN c int GENERATED ALWAYS AS (a + 1) STORED",
        "ALTER TABLE run ADD COLUMN c meu_dominio",
        "ALTER TABLE run ADD COLUMN c text, DROP COLUMN d",
        "ALTER TABLE run ADD COLUMN c text COMPRESSION lz4",
        "ALTER TABLE run ALTER COLUMN c SET STORAGE EXTERNAL",
        "ALTER TABLE run ADD COLUMN c text STORAGE EXTERNAL",
        "ALTER TABLE run ALTER COLUMN c TYPE bigint",
        "ALTER FOREIGN TABLE ft ADD COLUMN c text",
        "ALTER INDEX ix RENAME TO iy",
        "CREATE TABLE run_2026 PARTITION OF run FOR VALUES IN (2026)",
        "CREATE TABLE filha (id int) INHERITS (run)",
        "CREATE TABLE copia AS SELECT * FROM run",
        "CREATE TABLE share (id int); UPDATE run SET a = 1",
    ],
    ids=[
        "indice-sem-concurrently",
        "default",
        "not-null",
        "unique",
        "references",
        "check",
        "collate",
        "identity",
        "gerada",
        "tipo-do-projeto",
        "add-e-drop",
        "compression",
        "storage",
        "add-com-storage",
        "troca-de-tipo",
        "tabela-estrangeira",
        "alter-index",
        "particao",
        "heranca",
        "create-as",
        "segunda-instrucao",
    ],
)
def test_sql_fora_da_forma_minima_pede_a_marca(sql: str) -> None:
    assert unmarked_blocking_lines(_upgrade(f'op.execute("{sql}")')) == [2]


@pytest.mark.parametrize(
    "column",
    [
        'sa.Column("c", sa.Text(), nullable=False)',
        'sa.Column("c", sa.Text(), server_default="x")',
        'sa.Column("c", sa.Text(), server_default=sa.text("now()"))',
        'sa.Column("c", sa.Text(), unique=True)',
        'sa.Column("c", sa.Text(), index=True)',
        'sa.Column("c", sa.Uuid(), sa.ForeignKey("app_user.id"))',
        'sa.Column("c", sa.Boolean(create_constraint=True))',
        'sa.Column("c", sa.Boolean(create_constraint=C))',
        'sa.Column("c", sa.Enum("a", "b", name="e"))',
        'sa.Column("c", sa.Enum("a", "b", name="e", create_constraint=C))',
        'sa.Column("c", sa.String(length=TAMANHO))',
        'sa.Column("c", postgresql.JSONB(astext_type=sa.Text()))',
        'sa.Column("c", sa.ARRAY(sa.Enum("a", name="e")))',
        'sa.Column("c", TIPO)',
        'sa.Column("c", meus_tipos.Dinheiro())',
        "sa.Column(NOME, sa.Text())",
        'sa.Column("c", sa.Text(), nullable=NULO)',
        'sa.Column("c", sa.Text(), **opcoes)',
        'sa.Column("c", sa.Text(), comment=0)',
        'sa.Column("c", sa.Numeric(PRECISAO, 2))',
        'sa.Column("c", sa.PickleType())',
        'sa.Column("c", sa.Enum)',
        "coluna",
    ],
    ids=[
        "not-null",
        "default-literal",
        "default-sql",
        "unique",
        "index",
        "foreign-key",
        "boolean-com-check",
        "boolean-com-check-variavel",
        "enum",
        "enum-com-check-variavel",
        "tamanho-variavel",
        "opcao-de-tipo-desconhecida",
        "array-de-enum",
        "tipo-em-variavel",
        "tipo-do-projeto",
        "nome-em-variavel",
        "nullable-variavel",
        "kwargs",
        "comment-que-nao-e-texto",
        "precisao-variavel",
        "tipo-fora-da-lista",
        "tipo-inerte-fora-da-lista",
        "coluna-em-variavel",
    ],
)
def test_add_column_fora_da_forma_minima_pede_a_marca(column: str) -> None:
    assert unmarked_blocking_lines(_upgrade(f'op.add_column("run", {column})')) == [2]


@pytest.mark.parametrize(
    "column",
    [
        'sa.Column("c", sa.Text)',
        'sa.Column("c", sa.Text(), nullable=True, comment="nota")',
        'sa.Column("c", sa.Numeric(10, 2))',
        'sa.Column("c", sa.String(length=40))',
        'sa.Column("c", sa.DateTime(timezone=True))',
        'sa.Column("c", sa.Boolean())',
        'sa.Column("c", sa.ARRAY(sa.Text(), dimensions=1))',
        'sa.Column("c", postgresql.JSONB(none_as_null=True))',
        'sa.Column("c", postgresql.CIDR())',
    ],
    ids=[
        "tipo-sem-chamada",
        "nullable-e-comment",
        "numeric",
        "string",
        "timestamptz",
        "boolean-sem-check",
        "array",
        "jsonb",
        "cidr",
    ],
)
def test_add_column_na_forma_minima_passa(column: str) -> None:
    assert unmarked_blocking_lines(_upgrade(f'op.add_column("run", {column})')) == []


@pytest.mark.parametrize(
    "operation",
    [
        'op.create_index("ix", "run", ["a"])',
        'op.create_index("ix", "run", ["a"], postgresql_concurrently=False)',
        'op.create_index("ix", "run", ["a"], postgresql_concurrently=CONCORRENTE)',
        'op.create_index("ix", "run", ["a"], **opcoes)',
        "op.create_index(*argumentos, postgresql_concurrently=True)",
        'op.create_index("ix", "run", ["a"], postgresql_concurrently=True, postgresql_with={})',
        'op.create_table("t", sa.Column("id", sa.Integer()), postgresql_inherits="run")',
        'op.create_table("t", sa.Column("id", sa.Integer()), postgresql_partition_by="RANGE (id)")',
        'op.create_table(NOME, sa.Column("id", sa.Integer()))',
        'op.create_table("t", coluna)',
        'op.create_table("t", sa.Column("id", sa.Integer()), **opcoes)',
        'op.create_table("t", sa.Column("id", sa.Integer()), sa.Index("ix", "id"))',
        'op.add_column("run", sa.Column("c", sa.Text()), schema="s")',
        'op.add_column(table_name="run", column=sa.Column("c", sa.Text()))',
        'op.execute(f"CREATE TABLE {nome} (id int)")',
        "op.execute(sa.text(SQL))",
        "op.execute(\"SELECT '%s'\" % d(1))",
        'op.execute(sa.text("CREATE TABLE t (id int)").bindparams())',
        'op.execute("CREATE TABLE t (id int)", execution_options={})',
        'op.create_index("ix", "run", ["a"], "s", postgresql_concurrently=True)',
        'op.create_index(op.f("i", "x"), "run", ["a"], postgresql_concurrently=True)',
        'op.create_table("t", sa.ext.Column("id", sa.Integer()))',
    ],
    ids=[
        "indice-sem-concurrently",
        "concurrently-falso",
        "concurrently-variavel",
        "indice-com-kwargs",
        "indice-com-args",
        "opcao-de-indice-desconhecida",
        "heranca",
        "particao",
        "nome-de-tabela-variavel",
        "coluna-em-variavel",
        "tabela-com-kwargs",
        "index-do-sqlalchemy",
        "add-column-com-schema",
        "add-column-por-keyword",
        "f-string",
        "sql-em-variavel",
        "percent-com-chamada",
        "metodo-no-text",
        "execute-com-opcoes",
        "indice-com-schema-posicional",
        "f-com-dois-argumentos",
        "modulo-do-sqlalchemy-fora-da-lista",
    ],
)
def test_forma_nao_reconhecida_da_operacao_pede_a_marca(operation: str) -> None:
    assert unmarked_blocking_lines(_upgrade(operation)) == [2]


# Caminhos até o banco que não passam por `op.<operação da allowlist>`; cada um pede a marca.
@pytest.mark.parametrize(
    ("statements", "line"),
    [
        (['op.get_bind().execute(sa.text("CREATE INDEX ix ON run (a)"))'], 2),
        (['list(map(op.execute, ["CREATE INDEX ix ON run (a)"]))'], 2),
        (['functools.partial(op.execute, "CREATE INDEX ix ON run (a)")()'], 2),
        (["executar = op.execute", 'executar("CREATE INDEX ix ON run (a)")'], 3),
        (['sa.Index("ix", tabela.c.a).create(op.get_bind())'], 2),
        (["metadata.create_all(op.get_bind())"], 2),
        (["_T.Dom()"], 2),
        (["exec(\"op.create_index('ix', 'run', ['a'])\")"], 2),
        (['globals()["op"].create_index("ix", "run", ["a"])'], 2),
        (['getattr(op, "create_index")("ix", "run", ["a"])'], 2),
        (['op.__dict__["execute"]("CREATE INDEX ix ON run (a)")'], 2),
        (['(lambda: op.execute("CREATE TABLE t (id int)"))()'], 2),
        (['sa.event.listen(tabela, "after_create", gatilho)'], 2),
        (["sa.func()"], 2),
        (["with abrir():", "    pass"], 2),
        (["with contexto:", "    pass"], 2),
        (["with outro.get_context().autocommit_block():", "    pass"], 2),
        (["with sa.true().autocommit_block():", "    pass"], 2),
        (['with op.batch_alter_table("run") as batch:', "    batch.add_column(coluna)"], 2),
    ],
    ids=[
        "get-bind",
        "map",
        "partial",
        "alias",
        "index-create",
        "create-all",
        "classe-desconhecida",
        "exec",
        "globals",
        "getattr",
        "dict",
        "lambda",
        "evento",
        "func-sem-nome",
        "with-desconhecido",
        "with-de-nome",
        "autocommit-de-outro-objeto",
        "autocommit-de-chamada-inerte",
        "batch",
    ],
)
def test_chamada_fora_da_allowlist_pede_a_marca(statements: list[str], line: int) -> None:
    assert line in unmarked_blocking_lines(_upgrade(*statements))


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        # Default de parâmetro: `executar` não é função do módulo.
        (
            "def upgrade(executar=op.execute) -> None:\n"
            '    executar("CREATE INDEX ix ON run (a)")\n',
            [2],
        ),
        # Decorator e base de classe rodam código ao definir.
        ("def upgrade() -> None:\n    pass\n@registrar\ndef _passo() -> None:\n    pass\n", [3]),
        ("def upgrade() -> None:\n    pass\nclass Passo(Base):\n    pass\n", [3]),
        ("def upgrade() -> None:\n    pass\nclass Passo(metaclass=Meta):\n    pass\n", [3]),
        # Parâmetro com o nome de uma função do módulo: o nome deixa de ser só a função.
        (
            "def _passo() -> None:\n    pass\n"
            "def upgrade(_passo=op.get_bind) -> None:\n    _passo()\n",
            [4],
        ),
        # Função decorada não conta como função do módulo: a chamada também pede a marca.
        (
            "def upgrade() -> None:\n    _passo()\n@registrar\ndef _passo() -> None:\n    pass\n",
            [2, 3],
        ),
    ],
    ids=[
        "default-de-parametro",
        "decorator",
        "base-de-classe",
        "metaclasse",
        "parametro-com-nome-de-funcao",
        "funcao-decorada",
    ],
)
def test_codigo_que_roda_fora_da_allowlist_pede_a_marca(source: str, lines: list[int]) -> None:
    assert unmarked_blocking_lines(source + IMPORTS) == lines


def test_funcao_do_modulo_e_chamada_e_o_corpo_dela_e_lido() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    _coluna()\n"
        "def _coluna() -> None:\n"
        '    op.add_column("run", sa.Column("c", sa.Text()))\n'
        '    op.drop_column("run", "d")\n'
    )

    assert unmarked_blocking_lines(source + IMPORTS) == [5]


@pytest.mark.parametrize(
    "binding",
    [
        "    op = outro",
        "    with abrir() as op:\n        pass",
        "    try:\n        pass\n    except Exception as op:\n        pass",
        "    if op := ler():\n        pass",
        "    op, _ = ler()",
        "    for op in ler():\n        pass",
        "    import outro as op",
        "    from outro import op",
        "    match ler():\n        case op:\n            pass",
        "    match ler():\n        case [*op]:\n            pass",
        "    match ler():\n        case {**op}:\n            pass",
        "    _ = [op for op in ler()]",
        "    global op",
        "    del op",
        "    op.create_table = op.execute",
        "    op.__dict__['create_table'] = op.execute",
        "    def op() -> None:\n        pass",
        "    class op:\n        pass",
        "    def _f[op]() -> None:\n        pass",
        "    _ = lambda op: op",
    ],
    ids=[
        "atribuicao",
        "with",
        "except",
        "walrus",
        "desempacotamento",
        "for",
        "import-como",
        "from-import",
        "match-captura",
        "match-estrela",
        "match-resto",
        "compreensao",
        "global",
        "del",
        "atributo-atribuido",
        "item-atribuido",
        "def",
        "class",
        "parametro-de-tipo",
        "lambda",
    ],
)
def test_op_ligado_de_outra_forma_deixa_de_ser_o_do_alembic(binding: str) -> None:
    # O create_table fica sempre na linha 2; ligado de outra forma em qualquer lugar do módulo,
    # `op` pode ser qualquer coisa.
    source = (
        "def upgrade() -> None:\n"
        '    op.create_table("t", sa.Column("id", sa.Integer()))\n'
        f"def _outro() -> None:\n{binding}\n"
    )

    assert 2 in unmarked_blocking_lines(source + IMPORTS)


def test_op_so_com_o_import_do_alembic_e_confiavel() -> None:
    source = 'def upgrade() -> None:\n    op.create_table("t", sa.Column("id", sa.Integer()))\n'

    assert unmarked_blocking_lines(source + IMPORTS) == []
    assert unmarked_blocking_lines(source) == [2]
    assert unmarked_blocking_lines(source + "from outro import op\nimport sqlalchemy as sa\n") == [
        2
    ]
    assert unmarked_blocking_lines(
        source + "import alembic.op as op\nimport sqlalchemy as sa\n"
    ) == [2]


@pytest.mark.parametrize(
    "header",
    ["from alembic.op import *\n", "from outro import *\n"],
    ids=["do-alembic", "de-outro-modulo"],
)
def test_import_estrela_anula_a_confianca_em_todos_os_nomes(header: str) -> None:
    source = (
        "def upgrade() -> None:\n"
        '    op.create_table("t", sa.Column("id", sa.Integer()))\n'
        "    _passo()\n"
        "def _passo() -> None:\n"
        "    pass\n"
    )

    assert unmarked_blocking_lines(source + IMPORTS + header) == [2, 3]


def test_sa_religado_deixa_de_ser_o_sqlalchemy() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    op.create_table("t", sa.Column("id", sa.Integer()))\n'
        "sa = outro\n"
    )

    assert unmarked_blocking_lines(source + IMPORTS) == [2]


def test_marca_libera_a_linha() -> None:
    source = _upgrade(
        'op.create_index("a", "run", ["a"])  # lock-ok: run tem poucas linhas',
        "# lock-ok: idem",
        'op.create_index("b", "run", ["b"])',
    )

    assert unmarked_blocking_lines(source) == []


def test_so_downgrade_fica_de_fora() -> None:
    source = 'def downgrade() -> None:\n    op.create_index("ix", "run", ["a"])\n'

    assert unmarked_blocking_lines(source + IMPORTS) == []


def test_tabela_criada_na_mesma_funcao_dispensa_a_marca() -> None:
    # Ninguém lê nem escreve numa tabela que esta função acabou de criar.
    source = _upgrade(
        'op.create_table("share", sa.Column("id", sa.BigInteger(), nullable=False))',
        'op.create_index("ix_share_id", "share", ["id"])',
        'op.create_foreign_key("fk_share", "share", "run", ["run_id"], ["id"])',
        'op.create_check_constraint("ck", op.f("share"), "id > 0")',
        'op.create_index("ix_run_owner", "run", ["owner_id"])',
    )

    assert unmarked_blocking_lines(source) == [6]


def test_tabela_criada_com_marca_tambem_dispensa_a_marca_das_operacoes_seguintes() -> None:
    # A coluna vem de uma função do módulo: o create_table pede a marca, mas a tabela é nova.
    source = (
        "def upgrade() -> None:\n"
        '    op.create_table("share", _coluna())\n'
        '    op.create_index("ix_share_id", "share", ["id"])\n'
        "def _coluna() -> None:\n"
        '    return sa.Column("id", sa.BigInteger())\n'
    )

    assert unmarked_blocking_lines(source + IMPORTS) == [2]


@pytest.mark.parametrize(
    ("statements", "line"),
    [
        (
            [
                'op.create_table("share", sa.Column("id", sa.Integer()), if_not_exists=True)',
                'op.create_index("ix", "share", ["id"])',
            ],
            3,
        ),
        (
            [
                'op.create_table("share", sa.Column("id", sa.Integer()), schema="s")',
                'op.create_index("ix", "share", ["id"])',
            ],
            3,
        ),
        (
            [
                'op.create_table("share", sa.Column("id", sa.Integer()), **opcoes)',
                'op.create_index("ix", "share", ["id"])',
            ],
            3,
        ),
        (
            [
                'op.create_table("share", sa.Column("id", sa.Integer()))',
                'op.create_index("ix", "share", ["id"], schema="s")',
            ],
            3,
        ),
        (
            [
                'op.create_table("share", sa.Column("id", sa.Integer()))',
                'op.create_index(*["ix", "run"], "share")',
            ],
            3,
        ),
        (
            [
                'op.create_index("ix", "share", ["id"])',
                'op.create_table("share", sa.Column("id", sa.Integer()))',
            ],
            2,
        ),
        (
            [
                "if novo:",
                '    op.create_table("share", sa.Column("id", sa.Integer()))',
                'op.create_index("ix", "share", ["id"])',
            ],
            4,
        ),
        (
            [
                'outro.create_table("share", sa.Column("id", sa.Integer()))',
                'op.create_index("ix", "share", ["id"])',
            ],
            3,
        ),
    ],
    ids=[
        "if-not-exists",
        "outro-schema",
        "kwargs-no-create",
        "operacao-em-outro-schema",
        "args-na-operacao",
        "operacao-antes",
        "create-dentro-de-if",
        "create-que-nao-e-do-alembic",
    ],
)
def test_tabela_so_dispensa_a_marca_quando_e_certo_que_e_nova(
    statements: list[str], line: int
) -> None:
    assert line in unmarked_blocking_lines(_upgrade(*statements))


def test_sql_grande_e_analisado_em_tempo_linear() -> None:
    sql = "ALTER TABLE run " + ", ".join(f"ADD a{i} int" for i in range(2_000))
    started = time.monotonic()

    assert unmarked_blocking_lines(_upgrade(f'op.execute("{sql}")')) == []
    assert time.monotonic() - started < MAX_ANALYSIS_S


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        (
            'def upgrade() -> None:\n    op.execute("CREATE INDEX i ON run (a) -- # lock-ok: x")\n',
            [2],
        ),
        (
            "def upgrade() -> None:\n"
            '    op.execute("""SELECT 1\n'
            '# lock-ok: forjado""")\n'
            '    op.create_index("j", "run", ["a"])\n',
            [2, 4],
        ),
    ],
    ids=["na-linha", "linha-acima"],
)
def test_marca_dentro_de_um_texto_nao_vale(source: str, lines: list[int]) -> None:
    # Só comentário de verdade marca: o mesmo texto dentro do SQL não se autoriza. Na linha 2,
    # o `#` dentro do SELECT não é SQL que o parser aceite.
    assert unmarked_blocking_lines(source) == lines


def test_marca_sem_motivo_nao_vale() -> None:
    source = 'def upgrade() -> None:\n    op.create_index("ix", "run", ["a"])  # lock-ok:\n'

    assert unmarked_blocking_lines(source) == [2]


def test_marca_de_outra_operacao_na_linha_acima_nao_cobre() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    op.create_index("a", "run", ["a"])  # lock-ok: tabela nova\n'
        '    op.create_index("b", "run", ["b"])\n'
    )

    assert unmarked_blocking_lines(source) == [3]


@pytest.mark.parametrize(
    "operation",
    [
        'op.create_index("ix", "run", ["a"], postgresql_concurrently=True)',
        'op.drop_index("ix", table_name="run", postgresql_concurrently=True)',
        'op.execute("CREATE INDEX CONCURRENTLY ix ON run (a)")',
        'sql = "CREATE INDEX CONCURRENTLY ix ON run (a)"; op.execute(sql)',
        'op.execute("REINDEX (CONCURRENTLY) INDEX ix")',
        'op.execute("ALTER TABLE run DETACH PARTITION run_2026 CONCURRENTLY")',
        "op.execute('ALTER TABLE run DETACH PARTITION s.\"p 1\" CONCURRENTLY')",
    ],
)
def test_concurrently_fora_do_autocommit_e_recusado_mesmo_com_marca(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}  # lock-ok: tanto faz\n"

    assert concurrent_in_transaction_lines(source) == [2]


def test_concurrently_dentro_do_autocommit_passa() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    with op.get_context().autocommit_block():\n"
        '        op.drop_index("ix", table_name="run", postgresql_concurrently=True)\n'
        '        op.create_index("ix", "run", ["a"], postgresql_concurrently=True)\n'
    )

    assert concurrent_in_transaction_lines(source + IMPORTS) == []
    # O CREATE CONCURRENTLY está na allowlist; o DROP da limpeza não, e pede a marca.
    assert unmarked_blocking_lines(source + IMPORTS) == [3]


def test_concurrently_em_contexto_sem_autocommit_do_alembic_e_recusado() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    with outro.autocommit_block():  # lock-ok: contexto de outro objeto\n"
        '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix")  # lock-ok: limpeza\n'
        '        op.execute("CREATE INDEX CONCURRENTLY ix ON run (a)")\n'
    )

    assert unmarked_blocking_lines(source + IMPORTS) == []
    assert concurrent_in_transaction_lines(source + IMPORTS) == [3, 4]


def test_concurrently_em_autocommit_com_alias_do_alembic_passa() -> None:
    source = (
        "from alembic import op as operations\n"
        "def upgrade() -> None:\n"
        "    with operations.get_context().autocommit_block():\n"
        '        operations.execute("CREATE INDEX CONCURRENTLY ix ON run (a)")\n'
    )

    assert concurrent_in_transaction_lines(source) == []


def test_concurrently_junto_de_outra_instrucao_no_mesmo_execute_e_recusado() -> None:
    # Várias instruções numa mensagem só rodam numa transação implícita.
    source = (
        AUTOCOMMIT_HEADER
        + '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_a;'
        + ' CREATE INDEX CONCURRENTLY ix_a ON run (a)")\n'
    )

    assert concurrent_in_transaction_lines(source + IMPORTS) == [3]


def test_concurrently_com_ponto_e_virgula_no_fim_passa() -> None:
    source = AUTOCOMMIT_HEADER + '        op.execute("CREATE INDEX CONCURRENTLY ix ON run (a);")\n'

    assert concurrent_in_transaction_lines(source + IMPORTS) == []


def test_operacao_em_funcao_auxiliar_chamada_pelo_upgrade_e_lida() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    _add_index()\n\n\n"
        "def _add_index() -> None:\n"
        '    op.create_index("ix_run_owner", "run", ["owner_id"])\n'
    )

    assert unmarked_blocking_lines(source) == [6]


def test_funcao_auxiliar_so_do_downgrade_fica_de_fora() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    pass\n\n\n"
        "def downgrade() -> None:\n"
        "    _add_index()\n\n\n"
        "def _add_index() -> None:\n"
        '    op.create_index("ix_run_owner", "run", ["owner_id"])\n'
    )

    assert unmarked_blocking_lines(source) == []


@pytest.mark.parametrize(
    "caller",
    ["def upgrade() -> None:\n    _add_index()\n", "passos = [_add_index]\n"],
    ids=["chamada-pelo-upgrade", "citada-no-modulo"],
)
def test_funcao_auxiliar_do_downgrade_tambem_usada_fora_dele_e_lida(caller: str) -> None:
    source = (
        "def downgrade() -> None:\n"
        "    _add_index()\n"
        "def _add_index() -> None:\n"
        '    op.create_index("ix_run_owner", "run", ["owner_id"])\n'
        f"{caller}"
    )

    assert unmarked_blocking_lines(source) == [4]


@pytest.mark.parametrize(
    "operation",
    [
        # Repetição que faria um regex com dois trechos variáveis voltar atrás em cada posição.
        'op.execute("' + "ALTER TABLE run ADD a " * 2_000 + 'DEFAULT 0")',
        'op.execute(f"ALTER TABLE run {acao}")',
        'op.execute("ALTER TABLE run ADD COLUMN c int DEFAULT {}()".format(funcao))',
        'op.execute(f"ALTER TABLE run ADD COLUMN c {tipo}")',
        'op.execute(f"ALTER TABLE run ADD COLUMN c int DEFAULT {expr}")',
        'op.execute("SELECT \'x")',
    ],
    ids=[
        "sql-invalido-longo",
        "palavra-chave-desconhecida",
        "funcao-desconhecida-no-default",
        "tipo-desconhecido",
        "default-desconhecido",
        "aspas-sem-fechar",
    ],
)
def test_sql_que_o_check_nao_entende_pede_a_marca(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n" + IMPORTS
    started = time.monotonic()

    assert unmarked_blocking_lines(source) == [2]
    assert time.monotonic() - started < MAX_ANALYSIS_S


def test_concurrently_em_sql_que_o_parser_recusa_continua_valendo() -> None:
    create = 'op.execute(f"CREATE INDEX CONCURRENTLY ix ON run {cols}")\n'

    assert concurrent_in_transaction_lines(f"def upgrade() -> None:\n    {create}") == [2]
    # Sem nome resolvido, nenhum DROP anterior conta como limpeza.
    assert concurrent_index_without_cleanup_lines(
        AUTOCOMMIT_HEADER + f"        {create}" + IMPORTS
    ) == [3]


def test_texto_que_nem_o_scanner_aceita_conta_como_concorrente() -> None:
    source = 'def upgrade() -> None:\n    op.execute("SELECT \'x")\n'

    assert concurrent_in_transaction_lines(source) == [2]


def test_refresh_concorrente_que_o_parser_recusa_roda_em_transacao() -> None:
    # O CONCURRENTLY do REFRESH MATERIALIZED VIEW não exige autocommit, parseando ou não.
    refresh = 'op.execute(f"REFRESH MATERIALIZED VIEW CONCURRENTLY mv {opcao}")'
    source = f"def upgrade() -> None:\n    {refresh}\n"

    assert unmarked_blocking_lines(source) == [2]
    assert concurrent_in_transaction_lines(source) == []


AUTOCOMMIT_HEADER = "def upgrade() -> None:\n    with op.get_context().autocommit_block():\n"
CREATE_CONCURRENT = '        op.execute("CREATE INDEX CONCURRENTLY ix_a ON run (a)")\n'
DROP_CONCURRENT = (
    '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_a")  # lock-ok: limpeza\n'
)


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ('        op.create_index("ix_a", "run", ["a"], postgresql_concurrently=True)\n', 3),
        ('        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_a ON run (a)")\n', 3),
        ('        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_b")\n' + CREATE_CONCURRENT, 4),
        (CREATE_CONCURRENT + '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_a")\n', 3),
        # No PostgreSQL, "IX_A" entre aspas é outro índice: ix_a continua sem limpeza.
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"IX_A\"')\n"
            + CREATE_CONCURRENT,
            4,
        ),
        ('        sql = "CREATE INDEX CONCURRENTLY ix_a ON run (a)"\n        op.execute(sql)\n', 4),
        (
            '        name = "ix_a"\n'
            '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_b")\n'
            '        op.execute(f"CREATE INDEX CONCURRENTLY {name} ON run (a)")\n',
            5,
        ),
        (
            '        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {first()}")\n'
            '        op.execute(f"CREATE INDEX CONCURRENTLY {second()} ON run (a)")\n',
            4,
        ),
        (
            '        op.drop_index("Ix_A", "run", postgresql_concurrently=True, if_exists=True)\n'
            '        op.create_index("ix_a", "run", ["a"], postgresql_concurrently=True)\n',
            4,
        ),
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"my other\"')\n"
            "        op.execute('CREATE INDEX CONCURRENTLY \"my idx\" ON run (a)')\n",
            4,
        ),
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"a.b\"')\n"
            '        op.execute("CREATE INDEX CONCURRENTLY a.b ON run (a)")\n',
            4,
        ),
        (
            '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_a")\n'
            '        op.execute("CREATE INDEX CONCURRENTLY ix_a ON s.run (a)")\n',
            4,
        ),
        (
            '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ÍX_A")\n'
            "        op.execute('CREATE INDEX CONCURRENTLY \"íx_a\" ON run (a)')\n",
            4,
        ),
        (
            '        op.execute("CREATE INDEX CONCURRENTLY ON run (a)")\n',
            3,
        ),  # Sem IF EXISTS, o DROP falha no primeiro deploy, quando o índice ainda não existe.
        ('        op.execute("DROP INDEX CONCURRENTLY ix_a")\n' + CREATE_CONCURRENT, 4),
        ("        if cleanup:\n    " + DROP_CONCURRENT + CREATE_CONCURRENT, 5),
        (
            '        sql = "SELECT 1"\n'
            "        if cleanup:\n"
            '            sql = "DROP INDEX CONCURRENTLY IF EXISTS ix_a"\n'
            "        op.execute(sql)  # lock-ok: limpeza\n" + CREATE_CONCURRENT,
            7,
        ),
        (DROP_CONCURRENT + CREATE_CONCURRENT + CREATE_CONCURRENT, 5),
    ],
    ids=[
        "op-sem-drop",
        "sql-sem-drop",
        "drop-de-outro-indice",
        "drop-depois",
        "drop-com-aspas-e-outra-caixa",
        "sql-em-variavel",
        "f-string-com-outro-nome",
        "nomes-desconhecidos",
        "op-com-outra-caixa",
        "aspas-com-espaco-e-outro-nome",
        "ponto-entre-aspas-e-outro-schema",
        # O índice fica no schema da tabela; o DROP sem schema pode mirar outro.
        "drop-sem-schema-e-tabela-de-outro-schema",
        # O PostgreSQL só dobra a caixa de A a Z.
        "caixa-nao-ascii",
        "sem-nome",
        "drop-sem-if-exists",
        "drop-condicional",
        "drop-em-so-uma-alternativa",
        "drop-ja-consumido",
    ],
)
def test_indice_concorrente_sem_drop_previo_e_recusado(body: str, line: int) -> None:
    assert concurrent_index_without_cleanup_lines(AUTOCOMMIT_HEADER + body + IMPORTS) == [line]


@pytest.mark.parametrize(
    "body",
    [
        (
            '        op.drop_index("ix_a", "run", postgresql_concurrently=True, if_exists=True)\n'
            '        op.create_index("ix_a", "run", ["a"], postgresql_concurrently=True)\n'
        ),
        "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"ix_a\"')\n" + CREATE_CONCURRENT,
        '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS IX_A")\n' + CREATE_CONCURRENT,
        (
            '        name = "ix_a"\n'
            '        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")\n'
            '        op.execute(f"CREATE INDEX CONCURRENTLY {name} ON run (a)")\n'
        ),
        (
            '        op.drop_index("Ix_A", "run", postgresql_concurrently=True, if_exists=True)\n'
            '        op.create_index("Ix_A", "run", ["a"], postgresql_concurrently=True)\n'
        ),
        (
            '        op.execute("DROP INDEX CONCURRENTLY IF EXISTS IX_A")\n'
            '        op.create_index("ix_a", "run", ["a"], postgresql_concurrently=True)\n'
        ),
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS \"my idx\"')\n"
            "        op.execute('CREATE INDEX CONCURRENTLY \"my idx\" ON run (a)')\n"
        ),
        (
            "        op.execute('DROP INDEX CONCURRENTLY IF EXISTS public.\"ix_a\"')\n"
            '        op.execute("CREATE INDEX CONCURRENTLY ix_a ON PUBLIC.run (a)")\n'
        ),
        (
            "        op.drop_index(\n"
            '            "ix_a", "run", schema="s", postgresql_concurrently=True, if_exists=True\n'
            "        )\n"
            "        op.create_index(\n"
            '            "ix_a", "run", ["a"], schema="s", postgresql_concurrently=True\n'
            "        )\n"
        ),
        (DROP_CONCURRENT + CREATE_CONCURRENT) * 2,
        (
            '        sql = "DROP INDEX CONCURRENTLY IF EXISTS ix_a"\n'
            "        if quoted:\n"
            "            sql = 'DROP INDEX CONCURRENTLY IF EXISTS \"ix_a\"'\n"
            "        op.execute(sql)  # lock-ok: limpeza\n" + CREATE_CONCURRENT
        ),
    ],
    ids=[
        "op",
        "sql-com-aspas",
        "sql-sem-aspas-dobra-a-caixa",
        "f-string",
        "op-com-caixa",
        "sql-e-op",
        "aspas-com-espaco",
        "schema-da-tabela",
        "op-com-schema",
        "um-drop-por-create",
        "mesmo-drop-em-todas-as-alternativas",
    ],
)
def test_indice_concorrente_com_drop_previo_passa(body: str) -> None:
    assert concurrent_index_without_cleanup_lines(AUTOCOMMIT_HEADER + body + IMPORTS) == []


CREATE_IN_HELPER = (
    'op.create_index("ix", "run", ["a"], postgresql_concurrently=True)',
    'op.execute("CREATE INDEX CONCURRENTLY ix ON run (a)")',
)


@pytest.mark.parametrize("create", CREATE_IN_HELPER, ids=["create-index", "execute"])
def test_concurrently_em_funcao_auxiliar_chamada_dentro_do_bloco_e_recusado(create: str) -> None:
    # A limpeza só é conferida no bloco em que a criação está escrita.
    source = (
        "def upgrade() -> None:\n"
        "    with op.get_context().autocommit_block():\n"
        "        _indice()\n"
        "def _indice() -> None:\n"
        f"    {create}\n"
    )

    assert concurrent_in_transaction_lines(source + IMPORTS) == [5]


@pytest.mark.parametrize("create", CREATE_IN_HELPER, ids=["create-index", "execute"])
def test_concurrently_em_bloco_de_funcao_auxiliar_exige_a_limpeza(create: str) -> None:
    source = (
        "def upgrade() -> None:\n"
        "    _indice()\n"
        "def _indice() -> None:\n"
        "    with op.get_context().autocommit_block():\n"
        "        {drop}"
        f"        {create}\n"
    )
    drop = 'op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix")\n'

    assert concurrent_in_transaction_lines(source.format(drop="") + IMPORTS) == []
    assert concurrent_index_without_cleanup_lines(source.format(drop="") + IMPORTS) == [5]
    assert concurrent_index_without_cleanup_lines(source.format(drop=drop) + IMPORTS) == []


@pytest.mark.parametrize(
    "swap",
    [
        'op.drop_table("t")',
        'op.rename_table("t_antiga", "t")',
        'op.execute("ALTER TABLE t_antiga RENAME TO t")',
    ],
    ids=["drop-table", "rename-table", "execute-fora-da-allowlist"],
)
def test_tabela_trocada_depois_de_criada_deixa_de_dispensar_a_marca(swap: str) -> None:
    source = _upgrade(
        'op.create_table("t", sa.Column("c", sa.Integer()))',
        f"{swap}  # lock-ok: troca de propósito",
        'op.create_index("ix_c", "t", ["c"])',
    )

    assert unmarked_blocking_lines(source) == [4]


def test_troca_antes_da_criacao_nao_anula_a_dispensa() -> None:
    source = _upgrade(
        'op.drop_table("t")  # lock-ok: tabela antiga sai antes',
        'op.create_table("t", sa.Column("c", sa.Integer()))',
        'op.create_index("ix_c", "t", ["c"])',
    )

    assert unmarked_blocking_lines(source) == []
