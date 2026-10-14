from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import Settings
from tests.api.support import add_test_routes
from tests.identity.support import prepare_database


@pytest.fixture
def offline_client() -> Iterator[TestClient]:
    """Aplicação sem banco: basta para erros, roteamento e decodificação de comandos."""
    app = create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))
    add_test_routes(app)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def app(db_settings: Settings) -> FastAPI:
    app = create_app(db_settings)
    add_test_routes(app)
    prepare_database(db_settings, app.state.engine)
    return app


@pytest.fixture
def factory(app: FastAPI) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = app.state.session_factory
    return factory


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client
