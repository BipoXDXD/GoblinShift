"""Ambiente do Alembic: a URL vem das mesmas configurações da aplicação.

Cada revisão pendente roda na sua transação (`transaction_per_migration`), com `lock_timeout`
curto e `statement_timeout` finito (ADR 057). Uma migration que não consegue o lock desiste em
segundos, sem enfileirar a API atrás dela, e é repetida algumas vezes com espera sorteada; as
anteriores a ela ficam aplicadas. Esgotadas as tentativas, o comando falha e a API nova não
sobe. `-x lock_timeout_ms=<ms>` troca o `lock_timeout` de uma execução.

O `migrate` de uma imagem anterior encontra o banco numa revisão que ela não conhece depois de
um rollback do app. Pela política expand/contract (docs/GoblinShiftPlan.md, 3.8) essa imagem
tolera o schema mais novo, então o `upgrade` para o head avisa e termina com sucesso, sem
migrar. Em produção o único destino aceito é o head: descer o schema é restauração de backup.
"""

import logging
from logging.config import fileConfig

from alembic import context
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Connection, Engine

# As tabelas de cada módulo se registram no MetaData comum ao serem importadas.
import goblinshift.capacity.tables
import goblinshift.files.tables
import goblinshift.identity.tables
import goblinshift.presets.tables
import goblinshift.runs.tables  # noqa: F401
from goblinshift.platform.db import make_engine, metadata
from goblinshift.platform.migration_locks import (
    MIGRATION_LOCK_TIMEOUT_MS,
    run_with_lock_retries,
    session_settings,
)
from goblinshift.platform.schema import read_db_heads
from goblinshift.platform.settings import Settings

config = context.config
if config.config_file_name is not None:
    # Sem desligar os loggers já criados: no mesmo processo (testes, CLI), o padrão do
    # fileConfig silenciaria para sempre os logs da aplicação importados antes da migração.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata
log = logging.getLogger("alembic.env")


def run_migrations_offline() -> None:
    context.configure(
        url=Settings().database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = Settings()
    script = ScriptDirectory.from_config(config)
    image_heads = frozenset(script.get_heads())
    destination = _destination(script)
    goes_to_head = destination == image_heads
    # Sem destino (`current`, `check`, `history`) o comando só lê, e vale também em produção.
    if settings.environment == "production" and destination is not None and not goes_to_head:
        raise CommandError(
            "em produção o migrate só sobe para o head; descer o schema é restaurar o backup"
        )
    lock_timeout_ms = _lock_timeout_ms()
    engine = make_engine(settings)
    try:
        if goes_to_head:
            with engine.connect() as connection:
                if _db_is_ahead_of_image(connection, script, image_heads):
                    return
        run_with_lock_retries(lambda: _migrate(engine, lock_timeout_ms))
    finally:
        engine.dispose()


def _migrate(engine: Engine, lock_timeout_ms: int) -> None:
    """Uma rodada do comando numa conexão nova, cada migration na sua transação."""
    with engine.connect() as connection:
        for statement in session_settings(lock_timeout_ms):
            connection.exec_driver_sql(statement)
        # SET de sessão é transacional no PostgreSQL: sem o commit, o rollback da primeira
        # migration que falhar desfaria os tempos, e a conexão ficaria fora de transação aberta
        # para o Alembic abrir uma por migration.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


def _lock_timeout_ms() -> int:
    raw = context.get_x_argument(as_dictionary=True).get("lock_timeout_ms")
    if raw is None:
        return MIGRATION_LOCK_TIMEOUT_MS
    try:
        return int(raw)
    except ValueError:
        raise CommandError(f"-x lock_timeout_ms precisa ser inteiro, veio {raw!r}") from None


def _destination(script: ScriptDirectory) -> frozenset[str] | None:
    """Destino do comando resolvido ("head" vira a revisão, "base" vazio); None sem destino."""
    try:
        argument = context.get_revision_argument()
    except KeyError:
        # `current`, `history` e afins não têm destino.
        return None
    # O `stamp` passa o destino como tupla, que o Alembic devolve sem resolver ("head" literal).
    elements = argument if isinstance(argument, tuple) else (argument,)
    revisions: set[str] = set()
    for element in elements:
        resolved = script.as_revision_number(element) if element is not None else None
        if isinstance(resolved, str):
            revisions.add(resolved)
        elif resolved is not None:
            revisions.update(resolved)
    return frozenset(revisions)


def _db_is_ahead_of_image(
    connection: Connection, script: ScriptDirectory, image_heads: frozenset[str]
) -> bool:
    """Avisa e devolve True se o banco tem uma revisão que esta imagem não conhece."""
    db_heads = read_db_heads(connection)
    known = frozenset(revision.revision for revision in script.walk_revisions())
    unknown = db_heads - known
    if not unknown:
        return False
    log.warning(
        "banco na revisão %s, que esta imagem não conhece (head da imagem: %s): "
        "migração não aplicada; a imagem anterior tolera o schema mais novo (expand/contract)",
        ", ".join(sorted(unknown)),
        ", ".join(sorted(image_heads)),
    )
    return True


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
