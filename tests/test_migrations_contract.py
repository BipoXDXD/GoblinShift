"""Migration que remove ou renomeia coluna ou tabela declara que é a fase de contract.

Pela política expand/contract (docs/GoblinShiftPlan.md, 3.8), a imagem anterior roda contra o
schema novo depois de um rollback. Um DROP ou RENAME no `upgrade()` quebra essa imagem, então
só entra com a marca `# contract: <motivo>` na linha da operação ou na linha logo acima, depois
que nenhuma imagem em uso lê o que sai. O `downgrade()` fica de fora: ele desfaz um expand.
"""

import ast
import re
import tokenize
from io import StringIO
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
CONTRACT_MARK = re.compile(r"#\s*contract:\s*\S")
DESTRUCTIVE_OPS = frozenset({"drop_column", "drop_table", "rename_table"})
# Dentro de um ALTER TABLE, qualquer DROP ou RENAME conta, em qualquer ação da lista: no
# PostgreSQL o COLUMN é opcional nos dois. DROP de CONSTRAINT, DEFAULT, NOT NULL, IDENTITY e
# EXPRESSION e RENAME CONSTRAINT não são coluna nem tabela e ficam de fora.
DESTRUCTIVE_SQL = re.compile(
    r"\bDROP\s+(COLUMN|TABLE)\b"
    r"|\bALTER\s+TABLE\b[^;]*?\b("
    r"DROP\s++(?!CONSTRAINT\b|DEFAULT\b|NOT\s+NULL\b|IDENTITY\b|EXPRESSION\b)"
    r"|RENAME\b(?!\s+CONSTRAINT\b))",
    re.IGNORECASE,
)
SQL_HIDDEN_TOKEN = re.compile(
    r"--[^\r\n]*|(?P<block>/\*)"
    r"|\b[eE]'(?:''|\\[\s\S]|[^'\\])*'"
    r"|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\""
    r"|(?P<dollar>(?<![\w$])\$(?:[^\W\d]\w*)?\$)"
)
SQL_BLOCK_MARK = re.compile(r"/\*|\*/")
SQL_DO = re.compile(r"(?:^|;)\s*DO\b", re.IGNORECASE)


def unmarked_destructive_lines(source: str) -> list[int]:
    """Linhas do `upgrade()` com DROP/RENAME de coluna ou tabela sem a marca de contract."""
    lines = source.splitlines()
    upgrade = next(
        (
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        ),
        None,
    )
    if upgrade is None:
        return []
    marked_lines = {
        token.start[0]
        for token in tokenize.generate_tokens(StringIO(source).readline)
        if token.type == tokenize.COMMENT and CONTRACT_MARK.search(token.string)
    }
    found = []
    for node in ast.walk(upgrade):
        if isinstance(node, ast.Call) and _is_destructive(node):
            line = node.lineno
            # Na linha acima, só uma linha que é apenas o comentário: a marca de outra operação
            # não cobre esta.
            marked = line in marked_lines or (
                line - 1 in marked_lines and lines[line - 2].lstrip().startswith("#")
            )
            if not marked:
                found.append(line)
    return sorted(found)


def _is_destructive(call: ast.Call) -> bool:
    name = call.func.attr if isinstance(call.func, ast.Attribute) else None
    if name in DESTRUCTIVE_OPS:
        return True
    if name == "alter_column":
        return any(keyword.arg == "new_column_name" for keyword in call.keywords)
    if name in {"execute", "exec_driver_sql"}:
        return any(
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and _has_destructive_sql(arg.value)
            for arg in ast.walk(call)
        )
    return False


def _has_destructive_sql(sql: str) -> bool:
    visible = []
    offset = 0
    while token := SQL_HIDDEN_TOKEN.search(sql, offset):
        visible.append(sql[offset : token.start()])
        visible.append(" ")
        offset = token.end()
        if token["block"]:
            offset = _after_block_comment(sql, offset)
        elif delimiter := token["dollar"]:
            end = sql.find(delimiter, offset)
            offset = len(sql) if end < 0 else end + len(delimiter)
    visible.append(sql[offset:])
    keywords = "".join(visible)
    # DO executa o corpo literal. Mantém a checagem conservadora anterior nesses blocos.
    return bool(
        DESTRUCTIVE_SQL.search(keywords)
        or (SQL_DO.search(keywords) and DESTRUCTIVE_SQL.search(sql))
    )


def _after_block_comment(sql: str, offset: int) -> int:
    depth = 1
    for mark in SQL_BLOCK_MARK.finditer(sql, offset):
        depth += 1 if mark[0] == "/*" else -1
        if depth == 0:
            return mark.end()
    return len(sql)


@pytest.mark.parametrize("path", sorted(VERSIONS.glob("*.py")), ids=lambda p: p.name)
def test_migration_so_remove_ou_renomeia_com_a_marca_de_contract(path: Path) -> None:
    assert unmarked_destructive_lines(path.read_text()) == [], (
        f"{path.name}: DROP/RENAME no upgrade() sem `# contract: <motivo>`"
    )


@pytest.mark.parametrize(
    "operation",
    [
        'op.drop_column("run", "status")',
        'op.drop_table("run")',
        'op.rename_table("run", "job")',
        'op.alter_column("run", "status", new_column_name="state")',
        'op.execute("ALTER TABLE run DROP COLUMN status")',
        'op.execute("ALTER TABLE run RENAME COLUMN status TO state")',
        'op.execute("ALTER TABLE run RENAME TO job")',
        'op.execute("ALTER TABLE run DROP status")',
        'op.execute("ALTER TABLE IF EXISTS run DROP COLUMN IF EXISTS status")',
        'op.execute("ALTER TABLE run RENAME status TO state")',
        'op.execute("ALTER TABLE run ADD COLUMN y int, DROP status")',
        "op.execute('ALTER TABLE \"my run\" DROP status')",
        'op.execute(sa.text("DROP TABLE run"))',
        'op.get_bind().exec_driver_sql("ALTER TABLE run DROP COLUMN status")',
        'op.execute("ALTER TABLE run DROP COLUMN status /* # contract: anotação no SQL */")',
        'op.execute("DROP /* comentário */ TABLE run")',
        'op.execute("ALTER /* externo /* interno */ restante */ TABLE run DROP y")',
        """op.execute("ALTER TABLE run ADD COLUMN x text DEFAULT ';', DROP y")""",
        'op.execute("ALTER TABLE run ADD COLUMN x text DEFAULT $text$;$text$, DROP y")',
        'op.execute("DO $$ BEGIN DROP TABLE run; END $$")',
    ],
)
def test_operacao_destrutiva_sem_marca_e_recusada(operation: str) -> None:
    source = f"def upgrade() -> None:\n    {operation}\n"

    assert unmarked_destructive_lines(source) == [2]


@pytest.mark.parametrize(
    "source",
    [
        'def upgrade() -> None:\n    op.drop_column("run", "status")  # contract: sem leitor\n',
        'def upgrade() -> None:\n    # contract: sem leitor desde a 0012\n    op.drop_table("x")\n',
        'def upgrade() -> None:\n    op.add_column("run", sa.Column("x", sa.Text()))\n',
        'def upgrade() -> None:\n    op.alter_column("run", "status", nullable=True)\n',
        'def downgrade() -> None:\n    op.drop_column("run", "status")\n',
        'def upgrade() -> None:\n    op.execute("ALTER TABLE run DROP CONSTRAINT ck_status")\n',
        'def upgrade() -> None:\n    op.execute("ALTER TABLE run ALTER COLUMN x DROP DEFAULT")\n',
        'def upgrade() -> None:\n    op.execute("ALTER TABLE run RENAME CONSTRAINT a TO b")\n',
        (
            "def upgrade() -> None:\n"
            '    op.execute("ALTER TABLE run ALTER COLUMN x DROP /* x */ DEFAULT")\n'
        ),
        (
            "def upgrade() -> None:\n"
            """    op.execute("ALTER TABLE run ADD COLUMN x text DEFAULT 'DROP TABLE y'")\n"""
        ),
    ],
    ids=[
        "marca-na-linha",
        "marca-acima",
        "expand",
        "alter-sem-rename",
        "so-downgrade",
        "drop-constraint",
        "drop-default",
        "rename-constraint",
        "drop-default-com-comentario",
        "literal-com-texto-de-ddl",
    ],
)
def test_marca_expand_e_downgrade_passam(source: str) -> None:
    assert unmarked_destructive_lines(source) == []


def test_marca_sem_motivo_nao_vale() -> None:
    source = 'def upgrade() -> None:\n    op.drop_table("x")  # contract:\n'

    assert unmarked_destructive_lines(source) == [2]


def test_marca_de_outra_operacao_na_linha_acima_nao_cobre() -> None:
    source = (
        "def upgrade() -> None:\n"
        '    op.drop_table("a")  # contract: sem leitor\n'
        '    op.drop_table("b")\n'
    )

    assert unmarked_destructive_lines(source) == [3]
