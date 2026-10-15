"""O SQL do roteiro de abuso (docs/runbooks/abuso.md) roda contra o schema migrado.

Cada bloco `psql_gs -v nome="$variavel" <<'SQL' … SQL` do roteiro é executado pelo `psql` do
próprio contêiner do PostgreSQL, como o `psql_gs` faz na VPS, com valores de exemplo nas
variáveis. Uma coluna ou tabela renomeada no schema quebra este teste antes de quebrar o
roteiro no meio de um incidente.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from testcontainers.community.postgres import PostgresContainer

from goblinshift.platform.settings import Settings
from tests.conftest import run_migrations

pytestmark = pytest.mark.integration

RUNBOOK = Path(__file__).resolve().parents[1] / "docs" / "runbooks" / "abuso.md"
_PSQL_BLOCK = re.compile(r"^psql_gs (?P<args>.*?) <<'SQL'\n(?P<sql>.*?)^SQL$", re.M | re.S)
_VARIABLE = re.compile(r'-v (?P<name>\w+)="\$(?P<shell>\w+)"')
# Valores de exemplo das variáveis de shell que o roteiro passa ao psql.
EXAMPLE_VALUES = {
    "owner_id": "019a0000-0000-7000-8000-000000000001",
    "run_id": "019a0000-0000-7000-8000-000000000002",
    "public_id": "0123456789abcdef",
}


def psql_blocks(markdown: str) -> list[tuple[dict[str, str], str]]:
    """Os blocos `psql_gs` do roteiro: variáveis do psql (já com o exemplo) e o SQL."""
    blocks = []
    for match in _PSQL_BLOCK.finditer(markdown):
        variables = {
            found["name"]: EXAMPLE_VALUES[found["shell"]]
            for found in _VARIABLE.finditer(match["args"])
        }
        blocks.append((variables, match["sql"]))
    return blocks


def test_o_roteiro_tem_os_blocos_sql_esperados() -> None:
    """Sem isto, um bloco com outra sintaxe sumiria do teste em silêncio."""
    markdown = RUNBOOK.read_text(encoding="utf-8")

    blocks = psql_blocks(markdown)

    assert len(blocks) == markdown.count("<<'SQL'")
    assert len(blocks) == 4  # os quatro blocos do roteiro, contados à mão
    assert all(variables for variables, _ in blocks)


@pytest.fixture
def migrated_postgres(postgres: PostgresContainer, db_settings: Settings) -> PostgresContainer:
    run_migrations(db_settings, "head")
    return postgres


@pytest.mark.parametrize(
    ("variables", "sql"),
    psql_blocks(RUNBOOK.read_text(encoding="utf-8")),
    ids=lambda value: None if isinstance(value, dict) else value.split("\n", 1)[0][:40],
)
def test_bloco_sql_do_roteiro_roda_no_schema_migrado(
    migrated_postgres: PostgresContainer, variables: dict[str, str], sql: str
) -> None:
    docker = shutil.which("docker")
    assert docker is not None, "o teste usa o psql do contêiner pelo docker exec"
    container_id = migrated_postgres.get_wrapped_container().id
    psql_variables = [arg for name, value in variables.items() for arg in ("-v", f"{name}={value}")]

    result = subprocess.run(  # noqa: S603 - argumentos montados aqui, sem shell
        [
            docker,
            "exec",
            "-i",
            container_id,
            "psql",
            "-U",
            migrated_postgres.username,
            "-d",
            migrated_postgres.dbname,
            "-v",
            "ON_ERROR_STOP=1",
            *psql_variables,
        ],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "ERROR" not in result.stderr
