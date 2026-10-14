from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.settings import Settings
from tests.identity.support import prepare_database


@pytest.fixture
def engine(db_settings: Settings) -> Iterator[Engine]:
    """Banco em `head` e vazio (o container é compartilhado na sessão de testes)."""
    engine = make_engine(db_settings)
    prepare_database(db_settings, engine)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def storage(tmp_path: Path) -> FileStorage:
    return FileStorage(tmp_path / "storage")
