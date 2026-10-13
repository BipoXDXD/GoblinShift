"""Ambiente do Alembic: a URL vem das mesmas configurações da aplicação."""

from logging.config import fileConfig

from alembic import context

from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Ainda não há modelos; a partir da fatia 3 entra o MetaData das tabelas.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=Settings().database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(Settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                # Migrações podem demorar mais que o statement_timeout da aplicação.
                context.execute("SET LOCAL statement_timeout = 0")
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
