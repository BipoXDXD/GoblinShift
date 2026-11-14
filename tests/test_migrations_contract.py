"""Migration que remove ou renomeia coluna ou tabela declara que é a fase de contract.

Pela política expand/contract (docs/GoblinShiftPlan.md, 3.8), a imagem anterior roda contra o
schema novo depois de um rollback. Um DROP ou RENAME no `upgrade()` quebra essa imagem, então
só entra com a marca `# contract: <motivo>` na linha da operação ou na linha logo acima, depois
que nenhuma imagem em uso lê o que sai. O `downgrade()` fica de fora: ele desfaz um expand.
"""

import ast
import re
from pathlib import Path

import pytest

from tests.migration_source import is_marked, string_constants, upgrade_function

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
CONTRACT_MARK = re.compile(r"#\s*contract:\s*\S")
DESTRUCTIVE_OPS = frozenset({"drop_column", "drop_table", "rename_table"})
# Dentro de um ALTER TABLE, qualquer DROP ou RENAME conta, em qualquer ação da lista: no
# PostgreSQL o COLUMN é opcional nos dois. DROP de CONSTRAINT, DEFAULT, NOT NULL, IDENTITY e
# EXPRESSION e RENAME CONSTRAINT não são coluna nem tabela e ficam de fora.
DESTRUCTIVE_SQL = re.compile(
    r"\bDROP\s+(COLUMN|TABLE)\b"
    r"|\bALTER\s+TABLE\b[^;]*?\b("
    r"DROP\s+(?!CONSTRAINT\b|DEFAULT\b|NOT\s+NULL\b|IDENTITY\b|EXPRESSION\b)"
    r"|RENAME\b(?!\s+CONSTRAINT\b))",
    re.IGNORECASE,
)


def unmarked_destructive_lines(source: str) -> list[int]:
    """Linhas do `upgrade()` com DROP/RENAME de coluna ou tabela sem a marca de contract."""
    lines = source.splitlines()
    upgrade = upgrade_function(source)
    if upgrade is None:
        return []
    return sorted(
        node.lineno
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call)
        and _is_destructive(node)
        and not is_marked(lines, node.lineno, CONTRACT_MARK)
    )


def _is_destructive(call: ast.Call) -> bool:
    name = call.func.attr if isinstance(call.func, ast.Attribute) else None
    if name in DESTRUCTIVE_OPS:
        return True
    if name == "alter_column":
        return any(keyword.arg == "new_column_name" for keyword in call.keywords)
    if name == "execute":
        return any(DESTRUCTIVE_SQL.search(text) for text in string_constants(call))
    return False


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
