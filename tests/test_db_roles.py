"""Papéis de runtime do banco em `deploy/db-roles.sql` (ADR 055, proposto; issue #366).

O script é do dono e roda à mão: nada no repositório pode executá-lo sozinho num deploy. Os testes
de integração aplicam o script num PostgreSQL próprio (os papéis valem para o cluster inteiro e não
podem vazar para os outros testes) e conferem que, com ele, a aplicação faz o que faz hoje com
o papel único: a API e o worker como `gs_app`, a manutenção como `gs_maintenance`. E que nenhum
dos dois altera o `audit_event`, nem o `gs_app` o apaga.
"""

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

from goblinshift.entrypoints.maintenance import AccountJobs
from goblinshift.files.storage import FileStorage
from goblinshift.identity import AuditEventType, AuditOutcome, record_audit_event
from goblinshift.platform.db import make_engine, make_session_factory, unit_of_work
from goblinshift.platform.settings import Settings
from goblinshift.runs.maintenance import Maintenance
from tests.conftest import POSTGRES_IMAGE, run_migrations
from tests.runs.worker_support import (
    claim,
    coordinator,
    create_user,
    local_runner,
    queue_run,
    run_row,
)

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "db-roles.sql"
RUNTIME_LOGINS = ("gs_app", "gs_maintenance")
RUNTIME_ROLES = ("gs_runtime", *RUNTIME_LOGINS)
TEST_PASSWORD = "senha-so-do-teste"  # noqa: S105 - papel de um banco descartável
# Depois dos 90 dias de retenção do audit_event (1.7).
AFTER_AUDIT_RETENTION = timedelta(days=91)
# Onde algo que rode sozinho no deploy poderia chamar o script.
AUTOMATION_GLOBS = (
    ".github/**/*",
    "compose*.yaml",
    "deploy/**/*.json",
    "deploy/**/*.sh",
    "deploy/**/*.py",
    "deploy/**/*.service",
    "deploy/**/*.timer",
    "docker/**/*",
    "migrations/**/*",
    "scripts/**/*",
    "src/**/*",
    "Makefile",
    "alembic.ini",
    "pyproject.toml",
)


# O script não roda sozinho ------------------------------------------------------------------


def test_nenhuma_automacao_do_repositorio_chama_o_script() -> None:
    callers = [
        path.relative_to(ROOT).as_posix()
        for pattern in AUTOMATION_GLOBS
        for path in ROOT.glob(pattern)
        if path.is_file() and SCRIPT.name in path.read_text(encoding="utf-8", errors="ignore")
    ]

    assert callers == []


def test_script_nao_traz_senha() -> None:
    # A senha é posta depois, com `\password`, que manda só o hash.
    assert not re.search(r"PASSWORD\s+'", SCRIPT.read_text(), re.IGNORECASE)


def test_script_e_uma_transacao_so() -> None:
    statements = [line for line in SCRIPT.read_text().splitlines() if not line.startswith("--")]
    body = "\n".join(statements).strip()

    assert body.startswith("BEGIN;")
    assert body.endswith("COMMIT;")


# Integração com PostgreSQL real -------------------------------------------------------------


@pytest.fixture(scope="module")
def roles_postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as container:
        yield container


def _settings(container: PostgresContainer, user: str, password: str) -> Settings:
    return Settings(
        environment="test",
        db_host=container.get_container_host_ip(),
        db_port=int(container.get_exposed_port(5432)),
        db_user=user,
        db_password=SecretStr(password),
        db_name=container.dbname,
    )


@pytest.fixture(scope="module")
def owner(roles_postgres: PostgresContainer) -> Iterator[Engine]:
    """O papel do `migrate`: dono do esquema no head, que aplica o script duas vezes."""
    settings = _settings(roles_postgres, roles_postgres.username, roles_postgres.password)
    run_migrations(settings, "head")
    engine = make_engine(settings)
    for _ in range(2):
        _apply_script(settings)
    with engine.begin() as connection:
        for role in RUNTIME_LOGINS:
            connection.exec_driver_sql(f"ALTER ROLE {role} PASSWORD '{TEST_PASSWORD}'")
    yield engine
    engine.dispose()


def _apply_script(settings: Settings) -> None:
    # Como o `psql -f`: o texto inteiro numa ida, com o BEGIN e o COMMIT do próprio script.
    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
        dbname=settings.db_name,
        autocommit=True,
    ) as connection:
        connection.execute(SCRIPT.read_text().encode())


@pytest.fixture(scope="module")
def runtime_factory(
    roles_postgres: PostgresContainer, owner: Engine
) -> Iterator[Callable[[str], sessionmaker[Session]]]:
    engines: list[Engine] = []

    def factory_for(role: str) -> sessionmaker[Session]:
        engine = make_engine(_settings(roles_postgres, role, TEST_PASSWORD))
        engines.append(engine)
        return make_session_factory(engine)

    yield factory_for
    for engine in engines:
        engine.dispose()


def _privileges(owner: Engine, role: str, table: str) -> set[str]:
    query = text(
        "SELECT p FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) AS p "
        "WHERE has_table_privilege(:role, :table, p)"
    )
    with owner.connect() as connection:
        return set(connection.execute(query, {"role": role, "table": table}).scalars())


def _tables(owner: Engine) -> list[str]:
    with owner.connect() as connection:
        return list(
            connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
        )


DML = {"SELECT", "INSERT", "UPDATE", "DELETE"}


@pytest.mark.integration
@pytest.mark.parametrize("role", RUNTIME_ROLES)
def test_papel_de_runtime_nao_tem_atributo_especial(owner: Engine, role: str) -> None:
    query = text(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname = :role"
    )
    with owner.connect() as connection:
        attributes = connection.execute(query, {"role": role}).one()

    assert tuple(attributes) == (False, False, False, False, False)


@pytest.mark.integration
def test_gs_app_tem_dml_nas_tabelas_da_aplicacao_e_nao_mexe_no_audit_e_na_versao(
    owner: Engine,
) -> None:
    expected = {"alembic_version": {"SELECT"}, "audit_event": {"SELECT", "INSERT"}}

    privileges = {table: _privileges(owner, "gs_app", table) for table in _tables(owner)}

    assert privileges == {table: expected.get(table, DML) for table in privileges}


@pytest.mark.integration
def test_so_a_manutencao_apaga_do_audit_event(owner: Engine) -> None:
    assert _privileges(owner, "gs_maintenance", "audit_event") == {"SELECT", "INSERT", "DELETE"}


@pytest.mark.integration
def test_manutencao_so_trava_o_audit_event_pela_coluna_id(owner: Engine) -> None:
    # O `FOR UPDATE` do lote da retenção exige UPDATE em alguma coluna; só o `id`, que é
    # identidade GENERATED ALWAYS, e nenhuma coluna com o conteúdo do evento.
    query = text(
        "SELECT attname FROM pg_attribute WHERE attrelid = 'audit_event'::regclass "
        "AND attnum > 0 AND NOT attisdropped "
        "AND has_column_privilege(:role, 'audit_event', attname, 'UPDATE')"
    )
    with owner.connect() as connection:
        updatable = {
            role: set(connection.execute(query, {"role": role}).scalars())
            for role in RUNTIME_LOGINS
        }

    assert updatable == {"gs_app": set(), "gs_maintenance": {"id"}}


@pytest.mark.integration
def test_tabela_nova_de_migration_futura_ja_nasce_acessivel(owner: Engine) -> None:
    with owner.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE migracao_futura (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY)"
        )
    try:
        assert _privileges(owner, "gs_app", "migracao_futura") == DML
    finally:
        with owner.begin() as connection:
            connection.exec_driver_sql("DROP TABLE migracao_futura")


@pytest.mark.integration
def test_worker_e_manutencao_rodam_o_ciclo_de_uma_run_com_os_papeis_novos(
    runtime_factory: Callable[[str], sessionmaker[Session]], tmp_path: Path
) -> None:
    app = runtime_factory("gs_app")
    maintenance = runtime_factory("gs_maintenance")
    storage = FileStorage(tmp_path / "storage")
    runner = local_runner(tmp_path / "work")
    # API: conta, upload e Run na fila. Worker: claim, executor e conclusão.
    run_id = queue_run(app, storage, create_user(app, "papel-runtime"))
    claimed = claim(app, storage)
    coordinator(app, storage, runner).run(claimed)

    assert run_row(app, run_id)["status"] == "succeeded"
    # Manutenção: reconciliação e retenção das Runs, contas e janelas, sem erro de permissão.
    Maintenance(maintenance, storage, runner).run_cycle()
    AccountJobs(maintenance).run_cycle()


@pytest.mark.integration
def test_manutencao_apaga_o_audit_event_vencido(
    runtime_factory: Callable[[str], sessionmaker[Session]],
) -> None:
    app = runtime_factory("gs_app")
    user_id = create_user(app, "auditoria-vencida")
    _audit(app, user_id)
    later = datetime.now(UTC) + AFTER_AUDIT_RETENTION

    counts = AccountJobs(runtime_factory("gs_maintenance"), clock=lambda: later).run_cycle()

    assert counts["audit_events_removed"] >= 1


def _audit(factory: sessionmaker[Session], user_id: UUID) -> None:
    with unit_of_work(factory) as session:
        record_audit_event(
            session,
            event=AuditEventType.KEY_ISSUED,
            user_id=user_id,
            ip=None,
            outcome=AuditOutcome.SUCCESS,
            now=datetime.now(UTC),
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("role", "statement"),
    [
        ("gs_app", "UPDATE audit_event SET outcome = outcome"),
        ("gs_app", "DELETE FROM audit_event"),
        ("gs_app", "TRUNCATE audit_event"),
        ("gs_maintenance", "UPDATE audit_event SET outcome = outcome"),
        ("gs_maintenance", "UPDATE audit_event SET user_id = NULL, details = NULL"),
        ("gs_app", "UPDATE alembic_version SET version_num = version_num"),
        ("gs_app", "CREATE TABLE intrusa (id int)"),
        ("gs_maintenance", "CREATE TABLE intrusa (id int)"),
    ],
)
def test_papel_de_runtime_e_recusado(
    runtime_factory: Callable[[str], sessionmaker[Session]], role: str, statement: str
) -> None:
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        unit_of_work(runtime_factory(role)) as session,
    ):
        session.execute(text(statement))
