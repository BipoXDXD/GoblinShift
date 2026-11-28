"""A documentação de operação bate com o que roda em produção.

Mensagens de variável obrigatória (`${VAR:?mensagem}`) do Coolify apontam para onde a variável
vive na produção, não para os scripts do ambiente local; o comentário do `compose.yaml` não
descreve um syslog que o Nginx não tem; e o SQL do roteiro do OpenObserve que sobe o teto global
de armazenamento roda no schema migrado e acerta exatamente uma linha.
"""

import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import psycopg
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
README = ROOT / "README.md"
ROLLBACK = ROOT / "docs" / "runbooks" / "rollback.md"
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


def _db_password_section() -> str:
    readme = README.read_text(encoding="utf-8")
    start = readme.index("### Senha do banco")
    return readme[start : readme.index("\n## ", start)]


def test_senha_do_banco_nao_mostra_o_segredo_nem_deixa_shell_aberto() -> None:
    section = _db_password_section()

    shown = [
        line
        for line in section.splitlines()
        if re.search(r"\bcat db_password(\.new)?\b(?!\s*>)", line) and ">" not in line
    ]
    assert shown == []
    assert "sudo -u goblinshift -i" not in section


@pytest.mark.integration
def test_verificador_scram_do_roteiro_troca_a_senha(
    postgres: PostgresContainer, tmp_path: Path
) -> None:
    section = _db_password_section()
    # O bloco fica recuado dentro da lista do Markdown; renderizado, o recuo some.
    [script] = re.findall(r"python3 - \S+ <<'PY'\n(.*?)^ *PY$", section, re.M | re.S)
    [sql] = re.findall(
        r"psql_gs -v verifier=\"\$verifier\" <<'SQL'\n(.*?)^ *SQL$", section, re.M | re.S
    )
    password = "a" * 64
    secret = tmp_path / "db_password.new"
    secret.write_text(password + "\n")
    verifier = subprocess.run(  # noqa: S603 - o script é o do roteiro, sem shell
        [sys.executable, "-I", "-", str(secret)],
        input=textwrap.dedent(script),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert password not in verifier
    docker = shutil.which("docker")
    assert docker is not None
    container_id = postgres.get_wrapped_container().id

    def psql(statements: str, *variables: str) -> None:
        result = subprocess.run(  # noqa: S603 - argumentos montados aqui, sem shell
            [
                *(docker, "exec", "-i", container_id, "psql", "-U", postgres.username),
                *("-d", postgres.dbname, "-v", "ON_ERROR_STOP=1", *variables),
            ],
            input=statements,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

    psql("DROP ROLE IF EXISTS goblinshift; CREATE ROLE goblinshift LOGIN;")
    psql(textwrap.dedent(sql), "-v", f"verifier={verifier}")

    with psycopg.connect(
        host=postgres.get_container_host_ip(),
        port=int(postgres.get_exposed_port(5432)),
        user="goblinshift",
        password=password,
        dbname=postgres.dbname,
    ) as connection:
        assert connection.execute("SELECT current_user").fetchone() == ("goblinshift",)


def test_provisionamento_nao_depende_de_editor_definido() -> None:
    text = OPENOBSERVE_README.read_text(encoding="utf-8")

    assert re.findall(r'"?\$EDITOR"?', text) == []
    assert "sudoedit -u goblinshift /srv/goblinshift/secrets/openobserve.env" in text


def test_revert_do_rollback_mantem_as_migrations() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")
    commands = re.findall(r"git revert -[^`\n]*", text)

    assert commands and all("--no-commit" in command for command in commands)
    assert "git checkout HEAD -- migrations/versions/" in text


def test_rollback_manda_repetir_a_limpeza_ate_o_aviso_sumir() -> None:
    text = ROLLBACK.read_text(encoding="utf-8")

    assert 'até a mensagem "chegou ao limite" não aparecer mais' in text
