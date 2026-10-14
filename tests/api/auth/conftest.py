"""Fixtures do login: provedores falsos, as sessões gravadas e um navegador."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.platform.settings import Settings
from tests.api.auth.support import StoredSessions, browser, build_login_app, login_settings
from tests.identity.fake_providers import FakeProviders


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
def sessions(factory: sessionmaker[Session]) -> StoredSessions:
    return StoredSessions(factory)


@pytest.fixture
def login_app(db_settings: Settings, tmp_path: Path, fake: FakeProviders) -> Iterator[FastAPI]:
    app = build_login_app(login_settings(db_settings, tmp_path), fake)
    yield app
    # O lifespan de cada navegador já descarta o pool; as consultas feitas pelo teste depois que
    # um navegador fechou abrem outro, que só este dispose fecha.
    app.state.engine.dispose()


@pytest.fixture
def factory(login_app: FastAPI) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = login_app.state.session_factory
    return factory


@pytest.fixture
def client(login_app: FastAPI) -> Iterator[TestClient]:
    with browser(login_app) as client:
        yield client
