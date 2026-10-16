from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.settings import Settings
from tests.identity.support import prepare_database


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    """Banco em `head` e vazio; os presets saem junto com as contas (FK em cascata)."""
    engine = make_engine(db_settings)
    prepare_database(db_settings, engine)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)
