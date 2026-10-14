from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.settings import Settings
from tests.conftest import restore_capacity_seed, run_migrations


@pytest.fixture
def factory(db_settings: Settings) -> Iterator[sessionmaker[Session]]:
    """Banco em `head`, limpo antes de cada teste (o container é compartilhado na sessão)."""
    run_migrations(db_settings, "head")
    engine = make_engine(db_settings)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE app_user, stored_object RESTART IDENTITY CASCADE"))
        restore_capacity_seed(conn)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> FileStorage:
    return FileStorage(tmp_path / "storage")
