"""Migration nova não segura lock que trava a API sem declarar por quê.

DDL que reescreve ou varre a tabela sob ACCESS EXCLUSIVE ou SHARE (índice sem CONCURRENTLY,
constraint validada na hora, SET NOT NULL, troca de tipo, coluna com default volátil) para as
escritas, e às vezes as leituras, enquanto roda. A forma segura existe para cada caso
(docs/adr/057-travas-das-migrations.md). Quem precisa da forma bloqueante, por exemplo numa
tabela criada na mesma migration, marca `# lock-ok: <motivo>` na linha da operação ou na linha
só de comentário logo acima. CONCURRENTLY fora de `autocommit_block` falha no PostgreSQL dentro
da transação da migration, então nenhuma marca o libera.

As migrations até a 0018 são anteriores ao check e ficam de fora; o `downgrade()` também, como no
check de contract.
"""

import ast
import re
from pathlib import Path

import pytest

from tests.migration_source import is_marked, string_constants, upgrade_function

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
FIRST_CHECKED_REVISION = 19
REVISION_FILE = re.compile(r"^(\d{4})_\w+\.py$")
LOCK_OK_MARK = re.compile(r"#\s*lock-ok:\s*\S")

# Constraints que constroem índice sob lock: a forma segura é criar o índice com CONCURRENTLY
# antes e anexá-lo com `USING INDEX`, o que o Alembic não expressa nestas chamadas.
INDEX_BUILDING_CONSTRAINTS = frozenset(
    {"create_unique_constraint", "create_primary_key", "create_exclude_constraint"}
)
VALIDATING_CONSTRAINTS = frozenset({"create_foreign_key", "create_check_constraint"})
VOLATILE_DEFAULT = re.compile(
    r"gen_random_uuid|uuid_generate_v\d|uuidv\d|random\s*\(|clock_timestamp|timeofday|nextval",
    re.IGNORECASE,
)
BLOCKING_SQL = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bCREATE\s+(UNIQUE\s+)?INDEX\s+(?!CONCURRENTLY\b)",
        r"\bREINDEX\b(?![^;]*\bCONCURRENTLY\b)",
        r"\bADD\s+(CONSTRAINT\s+\S+\s+)?(FOREIGN\s+KEY|CHECK)\b(?![^;]*\bNOT\s+VALID\b)",
        r"\bADD\s+(CONSTRAINT\s+\S+\s+)?(UNIQUE|PRIMARY\s+KEY|EXCLUDE)\b(?!\s+USING\s+INDEX\b)",
        r"\bSET\s+NOT\s+NULL\b",
        r"\bALTER\s+(COLUMN\s+)?\S+\s+(SET\s+DATA\s+)?TYPE\b",
        r"\bADD\s+(COLUMN\s+)?[^,;]*?\bDEFAULT\s+[^,;]*?(" + VOLATILE_DEFAULT.pattern + r")",
        r"\bADD\s+(COLUMN\s+)?[^,;]*?\b(GENERATED\b[^,;]*?\b(IDENTITY|STORED)|"
        r"SMALLSERIAL|BIGSERIAL|SERIAL)\b",
        r"\bVACUUM\s+(\([^)]*\bFULL\b[^)]*\)|FULL\b)",
        r"\bCLUSTER\b",
    )
]
CONCURRENTLY_SQL = re.compile(r"\bCONCURRENTLY\b", re.IGNORECASE)


def checked_revisions(names: list[str]) -> list[str]:
    """Os arquivos de migration que o check cobre: da FIRST_CHECKED_REVISION em diante."""
    return sorted(
        name
        for name in names
        if (match := REVISION_FILE.match(name)) and int(match.group(1)) >= FIRST_CHECKED_REVISION
    )


def unmarked_blocking_lines(source: str) -> list[int]:
    """Linhas do `upgrade()` com DDL bloqueante sem a marca `# lock-ok:`."""
    lines = source.splitlines()
    upgrade = upgrade_function(source)
    if upgrade is None:
        return []
    return sorted(
        node.lineno
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call)
        and _is_blocking(node)
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


def _is_blocking(call: ast.Call) -> bool:
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
        return any(
            pattern.search(text) for text in string_constants(call) for pattern in BLOCKING_SQL
        )
    return False


def _adds_rewriting_column(call: ast.Call) -> bool:
    """`add_column` com default volátil ou identidade reescreve a tabela inteira."""
    for node in ast.walk(call):
        if not isinstance(node, ast.Call):
            continue
        if _callable_name(node) == "Identity":
            return True
        default = next((kw.value for kw in node.keywords if kw.arg == "server_default"), None)
        if default is not None and any(
            VOLATILE_DEFAULT.search(text) for text in string_constants(default)
        ):
            return True
    return False


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
