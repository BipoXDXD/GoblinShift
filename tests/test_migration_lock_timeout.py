"""Migration que não consegue o lock desiste em tempo limitado sem segurar a API (ADR 057).

Uma sessão concorrente segura a tabela que a migration 0018 altera. Enquanto a migration
espera o lock, as leituras que chegam depois esperam atrás dela; com `lock_timeout` curto essa
espera fica limitada, e o `migrate` termina com erro em vez de ficar pendurado.

Os testes rodam o CLI do Alembic num subprocesso, como o serviço `migrate` faz.
"""

import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

import pytest
from sqlalchemy import Connection, create_engine, text

from goblinshift.platform.settings import Settings
from tests.alembic_cli import (
    REPO_ROOT,
    alembic_command,
    alembic_env,
    db_version,
    head_revision,
    run_alembic,
    with_password_file,
)
from tests.conftest import downgrade_migrations, run_migrations

BEFORE_COLUMN_MIGRATION = "0017"
COLUMN_MIGRATION = "0018"
INDEX_MIGRATION = "0019"
# A 0018 acrescenta uma coluna em app_user e precisa de ACCESS EXCLUSIVE nela.
HOLD_APP_USER_LOCK = "LOCK TABLE app_user IN SHARE MODE"
# A 0019 constrói um índice comum em object_use, que precisa de SHARE.
HOLD_OBJECT_USE_LOCK = "LOCK TABLE object_use IN ROW EXCLUSIVE MODE"
READ_SQL = "SELECT count(*) FROM app_user"
TEST_LOCK_TIMEOUT_MS = 300
UPGRADE_ARGS = ("-x", f"lock_timeout_ms={TEST_LOCK_TIMEOUT_MS}", "upgrade", "head")
# Três tentativas de 0,3 s mais as esperas de 1 s e 2 s, com folga larga para o CI.
GIVE_UP_WITHIN_S = 20.0
# Uma leitura espera no máximo o lock_timeout atrás da migration; o resto é folga do CI.
MAX_READ_WAIT_S = 1.5
READ_INTERVAL_S = 0.02
RETRY_WARNING = "migration sem o lock"


@pytest.fixture
def before_column_migration(db_settings: Settings, tmp_path: Path) -> Iterator[Settings]:
    settings = with_password_file(db_settings, tmp_path)
    run_migrations(settings, "head")
    downgrade_migrations(settings, BEFORE_COLUMN_MIGRATION)
    yield settings
    # Outros testes usam o mesmo banco: ele volta ao head.
    run_migrations(settings, "head")


def test_migration_sem_o_lock_desiste_em_tempo_limitado_sem_segurar_as_leituras(
    before_column_migration: Settings,
) -> None:
    settings = before_column_migration
    with _holding_lock(settings, HOLD_APP_USER_LOCK), _read_waits(settings) as waits:
        result = run_alembic(settings, *UPGRADE_ARGS, timeout_s=GIVE_UP_WITHIN_S)

    assert result.returncode != 0
    assert "lock ocupado em 3 tentativas" in result.stderr
    assert db_version(settings) == BEFORE_COLUMN_MIGRATION
    assert waits, "nenhuma leitura concluída durante a migration"
    assert max(waits) < MAX_READ_WAIT_S


def test_lock_liberado_entre_as_tentativas_conclui_a_migration(
    before_column_migration: Settings,
) -> None:
    settings = before_column_migration
    _release_lock_after_retry(settings, HOLD_APP_USER_LOCK, UPGRADE_ARGS, BEFORE_COLUMN_MIGRATION)
    assert db_version(settings) == head_revision()


@pytest.mark.parametrize(
    ("operation", "relative", "start", "lock", "destination"),
    [
        ("upgrade", "+2", BEFORE_COLUMN_MIGRATION, HOLD_OBJECT_USE_LOCK, INDEX_MIGRATION),
        ("downgrade", "-2", INDEX_MIGRATION, HOLD_APP_USER_LOCK, BEFORE_COLUMN_MIGRATION),
    ],
)
def test_retry_preserva_destino_relativo_apos_commit_parcial(
    before_column_migration: Settings,
    operation: str,
    relative: str,
    start: str,
    lock: str,
    destination: str,
) -> None:
    settings = before_column_migration
    run_migrations(settings, start)
    args = ("-x", f"lock_timeout_ms={TEST_LOCK_TIMEOUT_MS}", operation, relative)

    _release_lock_after_retry(settings, lock, args, COLUMN_MIGRATION, environment="development")

    assert db_version(settings) == destination


def test_migration_anterior_fica_aplicada_quando_a_seguinte_perde_o_lock(
    before_column_migration: Settings,
) -> None:
    settings = before_column_migration
    with _holding_lock(settings, HOLD_OBJECT_USE_LOCK):
        result = run_alembic(settings, *UPGRADE_ARGS, timeout_s=GIVE_UP_WITHIN_S)

    assert result.returncode != 0
    assert "lock ocupado em 3 tentativas" in result.stderr
    assert db_version(settings) == COLUMN_MIGRATION

    resumed = run_alembic(settings, *UPGRADE_ARGS, timeout_s=GIVE_UP_WITHIN_S)
    assert resumed.returncode == 0, resumed.stderr
    assert db_version(settings) == head_revision()


@pytest.mark.parametrize("lock_timeout_ms", ["0", "-5"])
def test_lock_timeout_nao_positivo_e_recusado_com_mensagem(
    before_column_migration: Settings, lock_timeout_ms: str
) -> None:
    # 0 desliga o limite no PostgreSQL: a migration voltaria a esperar o lock para sempre.
    result = run_alembic(
        before_column_migration, "-x", f"lock_timeout_ms={lock_timeout_ms}", "upgrade", "head"
    )

    assert result.returncode != 0
    assert "lock_timeout_ms precisa ser positivo" in result.stderr
    assert "Traceback" not in result.stderr
    assert db_version(before_column_migration) == BEFORE_COLUMN_MIGRATION


def test_script_offline_leva_os_limites_de_sessao(
    before_column_migration: Settings,
) -> None:
    result = run_alembic(
        before_column_migration,
        *("-x", f"lock_timeout_ms={TEST_LOCK_TIMEOUT_MS}", "upgrade"),
        *(f"{BEFORE_COLUMN_MIGRATION}:head", "--sql"),
    )

    assert result.returncode == 0, result.stderr
    script = result.stdout
    assert f"SET lock_timeout = {TEST_LOCK_TIMEOUT_MS}" in script
    assert script.index("SET lock_timeout") < script.index("ALTER TABLE app_user")
    assert script.index("SET statement_timeout = 900000") < script.index("ALTER TABLE app_user")


@contextmanager
def _holding_lock(settings: Settings, statement: str) -> Iterator[Connection]:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text(statement))
            yield conn
            conn.rollback()
    finally:
        engine.dispose()


def _release_lock_after_retry(
    settings: Settings,
    lock: str,
    args: tuple[str, ...],
    partial_revision: str,
    *,
    environment: str = "production",
) -> None:
    with _holding_lock(settings, lock) as holder:
        process = subprocess.Popen(  # noqa: S603 - CLI do Alembic com argumentos fixos do teste
            alembic_command(*args),
            cwd=REPO_ROOT,
            env=alembic_env(settings, environment),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        # Sem o aviso, a leitura do stderr esperaria para sempre: o vigia encerra o processo.
        watchdog = threading.Timer(GIVE_UP_WITHIN_S, process.kill)
        watchdog.start()
        try:
            assert process.stderr is not None
            assert _wait_for_line(process.stderr, RETRY_WARNING)
            assert db_version(settings) == partial_revision
            holder.commit()
            _, stderr = process.communicate(timeout=GIVE_UP_WITHIN_S)
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()

    assert process.returncode == 0, stderr


@contextmanager
def _read_waits(settings: Settings) -> Iterator[list[float]]:
    """Leituras repetidas da tabela em outra sessão; a lista guarda quanto cada uma levou."""
    waits: list[float] = []
    stop = threading.Event()
    engine = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")

    def read() -> None:
        with engine.connect() as conn:
            while not stop.is_set():
                started = time.monotonic()
                conn.execute(text(READ_SQL))
                waits.append(time.monotonic() - started)
                stop.wait(READ_INTERVAL_S)

    reader = threading.Thread(target=read)
    reader.start()
    try:
        yield waits
    finally:
        stop.set()
        reader.join()
        engine.dispose()


def _wait_for_line(stream: IO[str], fragment: str) -> bool:
    """Lê o stream até uma linha com `fragment`; False se ele terminar antes."""
    return any(fragment in line for line in stream)
