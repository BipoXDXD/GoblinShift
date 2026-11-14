"""Migration nova não segura lock que trava a API sem declarar por quê.

DDL que reescreve ou varre a tabela sob ACCESS EXCLUSIVE ou SHARE (índice sem CONCURRENTLY,
constraint validada na hora, SET NOT NULL, troca de tipo, coluna com default volátil, única ou
indexada) para as escritas, e às vezes as leituras, enquanto roda. A forma segura existe para
cada caso (docs/adr/057-travas-das-migrations.md). Quem precisa da forma bloqueante marca
`# lock-ok: <motivo>` na linha da operação ou na linha só de comentário logo acima. Operação
sobre uma tabela criada no mesmo `upgrade()` não trava ninguém e dispensa a marca.
CONCURRENTLY fora de `autocommit_block` falha no PostgreSQL dentro da transação da migration,
então nenhuma marca o libera.

O check lê o `upgrade()` e as funções do módulo que ele chama, e resolve SQL guardado em
variável. As migrations até a 0018 são anteriores ao check e ficam de fora; o `downgrade()`
também, como no check de contract.
"""

import ast
import re
import time
from pathlib import Path

import pytest

from tests.migration_source import is_marked, string_constants, upgrade_function

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
FIRST_CHECKED_REVISION = 19
REVISION_FILE = re.compile(r"^(\d{4})_\w+\.py$")
LOCK_OK_MARK = re.compile(r"#\s*lock-ok:\s*\S")
# Folga larga para a máquina do CI: o caso linear leva milissegundos.
MAX_ANALYSIS_S = 2.0

# Constraints que constroem índice sob lock: a forma segura é criar o índice com CONCURRENTLY
# antes e anexá-lo com `USING INDEX`, o que o Alembic não expressa nestas chamadas.
INDEX_BUILDING_CONSTRAINTS = frozenset(
    {"create_unique_constraint", "create_primary_key", "create_exclude_constraint"}
)
VALIDATING_CONSTRAINTS = frozenset({"create_foreign_key", "create_check_constraint"})
# Posição e keyword da tabela-alvo de cada operação do Alembic.
TARGET_TABLE = {
    "create_index": (1, "table_name"),
    "create_unique_constraint": (1, "table_name"),
    "create_primary_key": (1, "table_name"),
    "create_exclude_constraint": (1, "table_name"),
    "create_foreign_key": (1, "source_table"),
    "create_check_constraint": (1, "table_name"),
    "alter_column": (0, "table_name"),
    "add_column": (0, "table_name"),
}
# Opções de `sa.Column` que viram índice ou constraint construídos sob lock.
INDEXED_COLUMN_FLAGS = ("unique", "index", "primary_key")
VOLATILE_DEFAULT = re.compile(
    r"\b(gen_random_uuid|uuid_generate_v\w+|uuidv\d|random|clock_timestamp|timeofday|nextval)\b",
    re.IGNORECASE,
)
SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
# Cada padrão roda numa cláusula só (ver `sql_clauses`) e tem no máximo um trecho variável
# longo, para o custo crescer em linha com o tamanho do SQL.
BLOCKING_CLAUSES = [
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"\bCREATE\s+(UNIQUE\s+)?INDEX\s+(?!CONCURRENTLY\b)",
        r"\bREINDEX\b(?!.*\bCONCURRENTLY\b)",
        r"\bADD\s+(CONSTRAINT\s+\S+\s+)?(FOREIGN\s+KEY|CHECK)\b(?!.*\bNOT\s+VALID\b)",
        r"\bADD\s+(CONSTRAINT\s+\S+\s+)?(UNIQUE|PRIMARY\s+KEY|EXCLUDE)\b(?!\s+USING\s+INDEX\b)",
        r"\bSET\s+NOT\s+NULL\b",
        r"\bALTER\s+(COLUMN\s+)?\S+\s+(SET\s+DATA\s+)?TYPE\b",
        r"\bVACUUM\s+(\([^)]*\bFULL\b[^)]*\)|FULL\b)",
        r"\bCLUSTER\b",
    )
]
ADD_COLUMN_SQL = re.compile(
    r"\bADD\s+(?!CONSTRAINT\b|UNIQUE\b|PRIMARY\b|FOREIGN\b|CHECK\b|EXCLUDE\b)", re.IGNORECASE
)
REWRITING_COLUMN_SQL = re.compile(
    r"\b(SMALLSERIAL|BIGSERIAL|SERIAL|UNIQUE|PRIMARY\s+KEY)\b", re.IGNORECASE
)
GENERATED_SQL = re.compile(r"\bGENERATED\b", re.IGNORECASE)
VIRTUAL_SQL = re.compile(r"\bVIRTUAL\b", re.IGNORECASE)
DEFAULT_SQL = re.compile(r"\bDEFAULT\b", re.IGNORECASE)
CONCURRENTLY_SQL = re.compile(r"\bCONCURRENTLY\b", re.IGNORECASE)


def checked_revisions(names: list[str]) -> list[str]:
    """Os arquivos de migration que o check cobre: da FIRST_CHECKED_REVISION em diante."""
    return sorted(
        name
        for name in names
        if (match := REVISION_FILE.match(name)) and int(match.group(1)) >= FIRST_CHECKED_REVISION
    )


def unmarked_blocking_lines(source: str) -> list[int]:
    """Linhas do `upgrade()`, e das funções que ele chama, com DDL bloqueante sem `# lock-ok:`."""
    tree = ast.parse(source)
    lines = source.splitlines()
    scope = _upgrade_scope(tree)
    created = _tables_created(scope)
    assignments = _string_assignments(tree)
    return sorted(
        node.lineno
        for function in scope
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and _target_table(node) not in created
        and _is_blocking(node, assignments)
        and not is_marked(lines, node.lineno, LOCK_OK_MARK)
    )


def concurrent_outside_autocommit_lines(source: str) -> list[int]:
    """Linhas do `upgrade()` que pedem CONCURRENTLY fora de um `autocommit_block()`."""
    upgrade = upgrade_function(source)
    if upgrade is None:
        return []
    inside_autocommit = {
        id(child)
        for node in ast.walk(upgrade)
        if isinstance(node, ast.With) and any(_is_autocommit(item) for item in node.items)
        for statement in node.body
        for child in ast.walk(statement)
    }
    return sorted(
        node.lineno
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call) and _is_concurrent(node) and id(node) not in inside_autocommit
    )


def sql_clauses(sql: str) -> list[str]:
    """O SQL sem comentários, dividido nas vírgulas e ponto e vírgula fora de parênteses e aspas."""
    text = SQL_COMMENT.sub(" ", sql)
    clauses: list[str] = []
    depth, quoted, start = 0, False, 0
    for index, char in enumerate(text):
        if char == "'":
            quoted = not quoted
        elif quoted:
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        elif char in ",;" and depth == 0:
            clauses.append(text[start:index])
            start = index + 1
    clauses.append(text[start:])
    return clauses


def _upgrade_scope(tree: ast.Module) -> list[ast.FunctionDef]:
    """O `upgrade()` e as funções do módulo que ele chama, direta ou indiretamente."""
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    pending, seen = ["upgrade"], []
    while pending:
        name = pending.pop()
        if name in seen or name not in functions or name == "downgrade":
            continue
        seen.append(name)
        pending.extend(
            node.func.id
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        )
    return [functions[name] for name in seen]


def _tables_created(scope: list[ast.FunctionDef]) -> frozenset[str]:
    return frozenset(
        node.args[0].value
        for function in scope
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and _operation(node) == "create_table"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    )


def _string_assignments(tree: ast.Module) -> dict[str, list[str]]:
    """Textos atribuídos a cada nome no módulo, para achar o SQL passado por variável."""
    texts: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                texts.setdefault(target.id, []).extend(string_constants(value))
    return texts


def _target_table(call: ast.Call) -> str | None:
    position, keyword = TARGET_TABLE.get(_operation(call) or "", (None, None))
    if position is None:
        return None
    if len(call.args) > position:
        node: ast.expr | None = call.args[position]
    else:
        node = next((kw.value for kw in call.keywords if kw.arg == keyword), None)
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_blocking(call: ast.Call, assignments: dict[str, list[str]]) -> bool:
    name = _operation(call)
    if name == "create_index":
        return not _keyword_is_true(call, "postgresql_concurrently")
    if name in INDEX_BUILDING_CONSTRAINTS:
        return True
    if name in VALIDATING_CONSTRAINTS:
        return not _keyword_is_true(call, "postgresql_not_valid")
    if name == "alter_column":
        return _keyword_is_false(call, "nullable") or _has_keyword(call, "type_")
    if name == "add_column":
        return _adds_rewriting_column(call)
    if name == "execute":
        return any(_is_blocking_sql(text) for text in _sql_texts(call, assignments))
    return False


def _adds_rewriting_column(call: ast.Call) -> bool:
    """`add_column` que reescreve a tabela ou constrói índice sob lock."""
    for node in ast.walk(call):
        if not isinstance(node, ast.Call):
            continue
        kind = _callable_name(node)
        if kind == "Identity" or (
            kind == "Computed" and _keyword_constant(node, "persisted") is not False
        ):
            return True
        if any(_keyword_is_true(node, flag) for flag in INDEXED_COLUMN_FLAGS):
            return True
        default = next((kw.value for kw in node.keywords if kw.arg == "server_default"), None)
        if default is not None and any(VOLATILE_DEFAULT.search(text) for text in _texts(default)):
            return True
    return False


def _is_blocking_sql(sql: str) -> bool:
    return any(
        any(pattern.search(clause) for pattern in BLOCKING_CLAUSES)
        or _adds_rewriting_column_sql(clause)
        for clause in sql_clauses(sql)
    )


def _adds_rewriting_column_sql(clause: str) -> bool:
    added = ADD_COLUMN_SQL.search(clause)
    if added is None:
        return False
    column = clause[added.end() :]
    if REWRITING_COLUMN_SQL.search(column):
        return True
    if GENERATED_SQL.search(column) and not VIRTUAL_SQL.search(column):
        return True
    default = DEFAULT_SQL.search(column)
    return default is not None and VOLATILE_DEFAULT.search(column, default.end()) is not None


def _sql_texts(call: ast.Call, assignments: dict[str, list[str]]) -> list[str]:
    referenced = [
        text
        for node in ast.walk(call)
        if isinstance(node, ast.Name)
        for text in assignments.get(node.id, [])
    ]
    return string_constants(call) + referenced


def _texts(node: ast.AST) -> list[str]:
    """Literais e nomes dentro de `node`: `sa.func.gen_random_uuid()` traz o nome da função."""
    names = [
        child.attr if isinstance(child, ast.Attribute) else child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute | ast.Name)
    ]
    return string_constants(node) + names


def _is_concurrent(call: ast.Call) -> bool:
    name = _operation(call)
    if name in {"create_index", "drop_index"}:
        return _keyword_is_true(call, "postgresql_concurrently")
    if name == "execute":
        return any(CONCURRENTLY_SQL.search(text) for text in string_constants(call))
    return False


def _is_autocommit(item: ast.withitem) -> bool:
    expression = item.context_expr
    return isinstance(expression, ast.Call) and _operation(expression) == "autocommit_block"


def _operation(call: ast.Call) -> str | None:
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _callable_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return call.func.id if isinstance(call.func, ast.Name) else None


def _has_keyword(call: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in call.keywords)


def _keyword_is_true(call: ast.Call, name: str) -> bool:
    return _keyword_constant(call, name) is True


def _keyword_is_false(call: ast.Call, name: str) -> bool:
    return _keyword_constant(call, name) is False


def _keyword_constant(call: ast.Call, name: str) -> object:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def test_migrations_novas_so_bloqueiam_com_a_marca_lock_ok() -> None:
    problems = {}
    for name in checked_revisions([path.name for path in VERSIONS.glob("*.py")]):
        source = (VERSIONS / name).read_text()
        blocking = unmarked_blocking_lines(source)
        concurrent = concurrent_outside_autocommit_lines(source)
        if blocking or concurrent:
            problems[name] = {
                "sem lock-ok": blocking,
                "CONCURRENTLY fora do autocommit": concurrent,
            }

    assert problems == {}


def test_todo_arquivo_de_migration_comeca_pelo_numero_da_revisao() -> None:
    # Um nome fora do padrão escaparia do check em silêncio.
    names = [path.name for path in VERSIONS.glob("*.py")]

    assert [name for name in names if not REVISION_FILE.match(name)] == []


def test_check_cobre_da_0019_em_diante() -> None:
    names = ["0001_a.py", "0018_b.py", "0019_c.py", "0020_d.py", "README.md"]

    assert checked_revisions(names) == ["0019_c.py", "0020_d.py"]


@pytest.mark.parametrize(
    "operation",
    [
        'op.create_index("ix_run_owner", "run", ["owner_id"])',
        'op.create_index("ix_run_owner", "run", ["owner_id"], unique=True)',
        'op.create_unique_constraint("uq_run_key", "run", ["key"])',
        'op.create_primary_key("pk_run", "run", ["id"])',
        'op.create_exclude_constraint("ex_run", "run", ("period", "&&"))',
        'op.create_foreign_key("fk_run_owner", "run", "account", ["owner_id"], ["id"])',
        'op.create_check_constraint("ck_run_size", "run", "size > 0")',
        'op.create_check_constraint("ck", "run", "size > 0", postgresql_not_valid=False)',
        'op.alter_column("run", "status", nullable=False)',
        'op.alter_column("run", "size", type_=sa.BigInteger())',
        (
            'op.add_column("run", sa.Column("token", sa.Uuid(),'
            ' server_default=sa.text("gen_random_uuid()")))'
        ),
        'op.add_column("run", sa.Column("seq", sa.BigInteger(), sa.Identity()))',
        (
            'op.add_column("run", sa.Column("at", sa.DateTime(),'
            ' server_default=sa.text("clock_timestamp()")))'
        ),
        'op.execute("CREATE INDEX ix_run_owner ON run (owner_id)")',
        'op.execute("CREATE UNIQUE INDEX ix_run_key ON run (key)")',
        'op.execute("REINDEX TABLE run")',
        (
            'op.execute("ALTER TABLE run ADD CONSTRAINT fk_owner FOREIGN KEY (owner_id)'
            ' REFERENCES account (id)")'
        ),
        'op.execute("ALTER TABLE run ADD CHECK (size > 0)")',
        'op.execute("ALTER TABLE run ADD CONSTRAINT uq_key UNIQUE (key)")',
        'op.execute("ALTER TABLE run ADD PRIMARY KEY (id)")',
        'op.execute("ALTER TABLE run ALTER COLUMN status SET NOT NULL")',
        'op.execute("ALTER TABLE run ALTER COLUMN size TYPE bigint")',
        'op.execute("ALTER TABLE run ALTER size SET DATA TYPE bigint")',
        'op.execute("ALTER TABLE run ADD COLUMN token uuid DEFAULT gen_random_uuid()")',
        'op.execute("ALTER TABLE run ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY")',
        'op.execute("ALTER TABLE run ADD COLUMN seq bigserial")',
        'op.execute("VACUUM FULL run")',
        'op.execute("CLUSTER run USING ix_run_owner")',
        'op.execute(sa.text("create index ix_run_owner on run (owner_id)"))',
        'op.add_column("run", sa.Column("key", sa.Text(), unique=True))',
        'op.add_column("run", sa.Column("key", sa.Text(), index=True))',
        'op.add_column("run", sa.Column("id2", sa.BigInteger(), primary_key=True))',
        (
            'op.add_column("run", sa.Column("token", sa.Uuid(),'
            " server_default=sa.func.gen_random_uuid()))"
        ),
        'op.add_column("run", sa.Column("total", sa.Integer(), sa.Computed("a + b")))',
        'op.execute("ALTER TABLE run ADD COLUMN key text UNIQUE")',
        'op.execute("ALTER TABLE run ADD COLUMN id2 bigint PRIMARY KEY")',
        'op.execute("ALTER TABLE run ADD COLUMN t int GENERATED ALWAYS AS (a + b) STORED")',
        'op.execute("ALTER TABLE run ADD COLUMN x text, ADD COLUMN k uuid DEFAULT uuidv7()")',
        'op.execute("ALTER TABLE run ADD CONSTRAINT fk FOREIGN KEY (o) REFERENCES a -- NOT VALID")',
        'op.execute("ALTER TABLE run ADD CHECK (size > 0) /* NOT VALID */")',
    ],
)
def test_ddl_bloqueante_sem_marca_e_recusado(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n"

    assert unmarked_blocking_lines(source) == [2]


@pytest.mark.parametrize(
    "operation",
    [
        'op.create_index("ix", "run", ["a"], postgresql_concurrently=True)',
        ('op.create_foreign_key("fk", "run", "account", ["o"], ["id"], postgresql_not_valid=True)'),
        'op.create_check_constraint("ck", "run", "size > 0", postgresql_not_valid=True)',
        'op.alter_column("run", "status", nullable=True)',
        'op.add_column("run", sa.Column("x", sa.Text()))',
        'op.add_column("run", sa.Column("on", sa.Boolean(), server_default=sa.false()))',
        'op.add_column("run", sa.Column("at", sa.DateTime(), server_default=sa.text("now()")))',
        'op.execute("CREATE INDEX CONCURRENTLY ix ON run (a)")',
        'op.execute("REINDEX INDEX CONCURRENTLY ix")',
        (
            'op.execute("ALTER TABLE run ADD CONSTRAINT fk FOREIGN KEY (o)'
            ' REFERENCES account (id) NOT VALID")'
        ),
        'op.execute("ALTER TABLE run VALIDATE CONSTRAINT fk")',
        'op.execute("ALTER TABLE run ADD CONSTRAINT uq UNIQUE USING INDEX ix")',
        'op.execute("ALTER TABLE run ALTER COLUMN status DROP NOT NULL")',
        "op.execute(\"ALTER TYPE run_status ADD VALUE 'paused'\")",
        'op.execute("UPDATE run SET size = 0 WHERE size IS NULL")',
        'op.add_column("run", sa.Column("t", sa.Integer(), sa.Computed("a", persisted=False)))',
        'op.execute("ALTER TABLE run ADD COLUMN x text, ADD COLUMN y int DEFAULT 0")',
        'op.execute("ALTER TABLE run ADD COLUMN t int GENERATED ALWAYS AS (a) VIRTUAL")',
        "op.execute(\"ALTER TABLE run ADD COLUMN n text DEFAULT 'a,b' -- UNIQUE\")",
    ],
    ids=[
        "indice-concorrente",
        "fk-not-valid",
        "check-not-valid",
        "nullable-true",
        "coluna-sem-default",
        "default-constante",
        "default-estavel",
        "sql-indice-concorrente",
        "sql-reindex-concorrente",
        "sql-fk-not-valid",
        "sql-validate",
        "sql-unique-using-index",
        "sql-drop-not-null",
        "sql-alter-type-enum",
        "sql-update",
        "coluna-gerada-virtual",
        "sql-varias-colunas-estaveis",
        "sql-coluna-gerada-virtual",
        "sql-virgula-entre-aspas-e-comentario",
    ],
)
def test_forma_segura_passa(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n"

    assert unmarked_blocking_lines(source) == []


@pytest.mark.parametrize(
    "source",
    [
        'def upgrade() -> None:\n    op.create_index("ix", "run", ["a"])  # lock-ok: tabela nova\n',
        'def upgrade() -> None:\n    # lock-ok: nova\n    op.create_index("i", "t", ["a"])\n',
        'def downgrade() -> None:\n    op.create_index("ix", "run", ["a"])\n',
    ],
    ids=["marca-na-linha", "marca-acima", "so-downgrade"],
)
def test_marca_e_downgrade_passam(source: str) -> None:
    assert unmarked_blocking_lines(source) == []


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
    ],
)
def test_concurrently_fora_do_autocommit_e_recusado_mesmo_com_marca(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}  # lock-ok: tanto faz\n"

    assert concurrent_outside_autocommit_lines(source) == [2]


def test_concurrently_dentro_do_autocommit_passa() -> None:
    source = (
        "def upgrade() -> None:\n"
        "    with op.get_context().autocommit_block():\n"
        '        op.drop_index("ix", table_name="run", postgresql_concurrently=True)\n'
        '        op.create_index("ix", "run", ["a"], postgresql_concurrently=True)\n'
    )

    assert concurrent_outside_autocommit_lines(source) == []
    assert unmarked_blocking_lines(source) == []


def test_sql_guardado_em_variavel_e_lido() -> None:
    source = (
        'INDEX_SQL = "CREATE INDEX ix_run_owner ON run (owner_id)"\n\n\n'
        "def upgrade() -> None:\n"
        "    op.execute(sa.text(INDEX_SQL))\n"
    )

    assert unmarked_blocking_lines(source) == [5]


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


def test_tabela_criada_no_mesmo_upgrade_dispensa_a_marca() -> None:
    # Ninguém lê nem escreve numa tabela que esta migration acabou de criar.
    source = (
        "def upgrade() -> None:\n"
        '    op.create_table("share", sa.Column("id", sa.BigInteger(), primary_key=True))\n'
        '    op.create_index("ix_share_id", "share", ["id"])\n'
        '    op.create_foreign_key("fk_share", "share", "run", ["run_id"], ["id"])\n'
        '    op.create_index("ix_run_owner", "run", ["owner_id"])\n'
    )

    assert unmarked_blocking_lines(source) == [5]


def test_sql_grande_e_analisado_em_tempo_linear() -> None:
    # Entrada que faria um regex com dois trechos variáveis voltar atrás em cada posição.
    sql = "ALTER TABLE run ADD a " * 2_000 + "DEFAULT 0"
    source = f'def upgrade() -> None:\n    op.execute("{sql}")\n'
    started = time.monotonic()

    assert unmarked_blocking_lines(source) == []
    assert time.monotonic() - started < MAX_ANALYSIS_S
