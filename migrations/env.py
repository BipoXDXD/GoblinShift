"""Ambiente do Alembic: a URL vem das mesmas configurações da aplicação.

Todas as revisões pendentes rodam numa transação só (`begin_transaction` abaixo): uma falha no
meio desfaz o lote inteiro, e o banco fica na revisão em que estava (ADR 056).

O `migrate` de uma imagem anterior encontra o banco numa revisão que ela não conhece depois de
um rollback do app. Pela política expand/contract (docs/GoblinShiftPlan.md, 3.8) essa imagem
tolera o schema mais novo, então o `upgrade` para o head avisa e termina com sucesso, sem
migrar. Em produção o único destino aceito é o head: descer o schema é restauração de backup.
"""

import logging
from logging.config import fileConfig

from alembic import command, context
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import Connection

# As tabelas de cada módulo se registram no MetaData comum ao serem importadas.
import goblinshift.capacity.tables
import goblinshift.files.tables
import goblinshift.identity.tables
import goblinshift.presets.tables
import goblinshift.runs.tables  # noqa: F401
from goblinshift.platform.db import make_engine, metadata
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
    settings = Settings()
    script = ScriptDirectory.from_config(config)
    _check_production_destination(settings, _destination(script), frozenset(script.get_heads()))
    context.configure(
        url=settings.database_url.render_as_string(hide_password=False),
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
    _check_production_destination(settings, destination, image_heads)
    engine = make_engine(settings)
    try:
        with engine.connect() as connection:
            if goes_to_head and _db_is_ahead_of_image(connection, script, image_heads):
                return
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                # Migrações podem demorar mais que o statement_timeout da aplicação.
                context.execute("SET LOCAL statement_timeout = 0")
                context.run_migrations()
    finally:
        engine.dispose()


def _check_production_destination(
    settings: Settings, destination: frozenset[str] | None, image_heads: frozenset[str]
) -> None:
    if settings.environment != "production":
        return
    # O CLI do Alembic põe o comando em cmd_opts.cmd, inclusive no modo --sql.
    cli_command = getattr(config.cmd_opts, "cmd", (None,))[0]
    if cli_command is command.downgrade or (destination is not None and destination != image_heads):
        raise CommandError(
            "em produção o migrate só sobe para o head; descer o schema é restaurar o backup"
        )


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
    # A leitura abre uma transação implícita; sem fechá-la, o `begin_transaction` do Alembic
    # entraria nela, e as migrações seriam desfeitas quando a conexão fechasse.
    connection.rollback()
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
