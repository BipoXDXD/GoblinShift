"""Migrações são autocontidas: importam o que usam e não dependem do código da aplicação.

Uma migração roda anos depois de escrita, contra o código de outra época. Se ela lê um submódulo
do SQLAlchemy por atributo (`sa.dialects.postgresql`) sem importá-lo, funciona só enquanto algum
outro módulo (hoje o `alembic.op`) o carrega antes; se importa `goblinshift`, muda de efeito quando
o modelo muda.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
MIGRATIONS = sorted(VERSIONS.glob("[0-9]*.py"))

# Percorre cada cadeia num interpretador que só fez `import sqlalchemy`, sem o que o pytest e o
# Alembic já deixaram carregado neste processo; imprime as que não resolvem.
_RESOLVE_CHAINS = """
import sys
import sqlalchemy

for chain in sys.argv[1:]:
    target = sqlalchemy
    try:
        for name in chain.split("."):
            target = getattr(target, name)
    except AttributeError:
        print(chain)
"""


def sqlalchemy_alias(tree: ast.Module) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlalchemy":
                    return alias.asname or alias.name
    return None


def attribute_chain(node: ast.Attribute) -> list[str] | None:
    names: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        names.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    names.append(current.id)
    return list(reversed(names))


def chains_from_sqlalchemy(source: str) -> set[str]:
    """Cadeias `sa.x.y` usadas no arquivo, sem o alias (`x.y`)."""
    tree = ast.parse(source)
    alias = sqlalchemy_alias(tree)
    if alias is None:
        return set()
    # Só as cadeias mais longas: `sa.a.b` já cobre `sa.a`.
    inner = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    chains: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or id(node) in inner:
            continue
        chain = attribute_chain(node)
        if chain is not None and chain[0] == alias:
            chains.add(".".join(chain[1:]))
    return chains


def unresolved_with_bare_import(chains: set[str]) -> list[str]:
    result = subprocess.run(  # noqa: S603 - interpretador atual, sem shell
        [sys.executable, "-I", "-c", _RESOLVE_CHAINS, *sorted(chains)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


def imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_finds_the_migrations() -> None:
    assert len(MIGRATIONS) >= 11


def test_flags_submodule_read_through_the_package_attribute() -> None:
    chains = chains_from_sqlalchemy(
        "import sqlalchemy as sa\nx = sa.dialects.postgresql.JSONB()\ny = sa.Text()\n"
    )

    assert chains == {"dialects.postgresql.JSONB", "Text"}
    assert unresolved_with_bare_import(chains) == ["dialects.postgresql.JSONB"]


@pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda path: path.stem)
def test_migration_reads_only_what_import_sqlalchemy_loads(migration: Path) -> None:
    chains = chains_from_sqlalchemy(migration.read_text(encoding="utf-8"))

    assert unresolved_with_bare_import(chains) == []


@pytest.mark.parametrize("migration", MIGRATIONS, ids=lambda path: path.stem)
def test_migration_does_not_import_application_code(migration: Path) -> None:
    modules = imported_modules(migration.read_text(encoding="utf-8"))

    assert not {m for m in modules if m == "goblinshift" or m.startswith("goblinshift.")}
