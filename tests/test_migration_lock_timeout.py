"""Migration que não consegue o lock desiste em tempo limitado sem segurar a API (ADR 057).

Uma sessão concorrente segura a tabela que a última migration altera. Enquanto a migration
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
    previous_revision,
    run_alembic,
    with_password_file,
)
from tests.conftest import downgrade_migrations, run_migrations

# A migration do head acrescenta uma coluna em app_user e precisa de ACCESS EXCLUSIVE nela.
HOLD_LOCK_SQL = "LOCK TABLE app_user IN SHARE MODE"
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
def one_behind(db_settings: Settings, tmp_path: Path) -> Iterator[Settings]:
    settings = with_password_file(db_settings, tmp_path)
    run_migrations(settings, "head")
    downgrade_migrations(settings, "-1")
    yield settings
    # Outros testes usam o mesmo banco: ele volta ao head.
    run_migrations(settings, "head")


def test_migration_sem_o_lock_desiste_em_tempo_limitado_sem_segurar_as_leituras(
    one_behind: Settings,
) -> None:
    with _holding_share_lock(one_behind), _read_waits(one_behind) as waits:
        result = run_alembic(one_behind, *UPGRADE_ARGS, timeout_s=GIVE_UP_WITHIN_S)

    assert result.returncode != 0
    assert "lock ocupado em 3 tentativas" in result.stderr
    assert db_version(one_behind) == previous_revision()
    assert waits, "nenhuma leitura concluída durante a migration"
    assert max(waits) < MAX_READ_WAIT_S


def test_lock_liberado_entre_as_tentativas_conclui_a_migration(one_behind: Settings) -> None:
    with _holding_share_lock(one_behind) as holder:
        process = subprocess.Popen(  # noqa: S603 - CLI do Alembic com argumentos fixos do teste
            alembic_command(*UPGRADE_ARGS),
            cwd=REPO_ROOT,
            env=alembic_env(one_behind),
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
            holder.commit()
            _, stderr = process.communicate(timeout=GIVE_UP_WITHIN_S)
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()

    assert process.returncode == 0, stderr
    assert db_version(one_behind) == head_revision()


@contextmanager
def _holding_share_lock(settings: Settings) -> Iterator[Connection]:
    """Uma sessão que segura a tabela em SHARE: deixa ler, mas barra o ALTER TABLE."""
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            conn.execute(text(HOLD_LOCK_SQL))
            yield conn
            conn.rollback()
    finally:
        engine.dispose()


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
