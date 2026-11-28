"""A documentação de operação bate com o que roda em produção.

Mensagens de variável obrigatória (`${VAR:?mensagem}`) do Coolify apontam para onde a variável
vive na produção, não para os scripts do ambiente local; o comentário do `compose.yaml` não
descreve um syslog que o Nginx não tem; e o SQL do roteiro do OpenObserve que sobe o teto global
de armazenamento roda no schema migrado e acerta exatamente uma linha.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from testcontainers.community.postgres import PostgresContainer

from goblinshift.platform.settings import Settings
from tests.conftest import run_migrations
from tests.test_runbook_abuse_sql import psql_blocks

ROOT = Path(__file__).resolve().parents[1]
COOLIFY = ROOT / "deploy" / "coolify"
COMPOSE = ROOT / "compose.yaml"
NGINX_TEMPLATE = ROOT / "docker" / "nginx" / "templates" / "default.conf.template"
OPENOBSERVE_README = ROOT / "deploy" / "openobserve" / "README.md"
# `${VAR:?mensagem}`: a mensagem vai até a chave que fecha.
_REQUIRED_MESSAGE = re.compile(r"\$\{\w+:\?(?P<message>[^}]*)\}")
_DEV_ONLY = ("dev-secrets.sh", "secrets/app.env")


def test_mensagens_do_coolify_nao_apontam_para_o_dev() -> None:
    messages = [
        (path.name, found["message"])
        for path in sorted(COOLIFY.glob("*.json"))
        for found in _REQUIRED_MESSAGE.finditer(path.read_text(encoding="utf-8"))
    ]

    assert messages, "nenhuma mensagem `:?` encontrada: o padrão do teste ficou velho"
    assert [(name, msg) for name, msg in messages if any(dev in msg for dev in _DEV_ONLY)] == []


def test_compose_nao_descreve_syslog_que_nao_existe() -> None:
    if "syslog:" in NGINX_TEMPLATE.read_text(encoding="utf-8"):
        pytest.skip("o Nginx já manda o access log por syslog")

    assert "manda o access log por syslog" not in COMPOSE.read_text(encoding="utf-8")


def _global_ceiling_block() -> tuple[dict[str, str], str]:
    blocks = [
        (variables, sql)
        for variables, sql in psql_blocks(OPENOBSERVE_README.read_text(encoding="utf-8"))
        if "capacity_pool" in sql
    ]
    assert len(blocks) == 1, "o roteiro precisa de exatamente um bloco que sobe o teto global"
    return blocks[0]


def test_bloco_do_teto_global_existe_e_so_altera_o_pool_global() -> None:
    _, sql = _global_ceiling_block()

    assert "WHERE scope = 'global' AND resource = 'storage_bytes'" in sql
    assert "RETURNING max_amount" in sql


@pytest.mark.integration
def test_sql_do_teto_global_atualiza_uma_linha(
    postgres: PostgresContainer, db_settings: Settings
) -> None:
    run_migrations(db_settings, "head")
    docker = shutil.which("docker")
    assert docker is not None, "o teste usa o psql do contêiner pelo docker exec"
    container_id = postgres.get_wrapped_container().id
    variables, sql = _global_ceiling_block()
    psql_variables = [arg for name, value in variables.items() for arg in ("-v", f"{name}={value}")]

    result = subprocess.run(  # noqa: S603 - argumentos montados aqui, sem shell
        [
            docker,
            "exec",
            "-i",
            container_id,
            "psql",
            "-U",
            postgres.username,
            "-d",
            postgres.dbname,
            "-v",
            "ON_ERROR_STOP=1",
            *psql_variables,
        ],
        input=f"BEGIN;\n{sql}ROLLBACK;\n",
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "(1 row)" in result.stdout
    assert variables["bytes"] in result.stdout
