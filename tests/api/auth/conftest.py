"""Fixtures do login: provedores falsos, sessão da Frente B em memória e um navegador."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api import auth_login
from goblinshift.platform.settings import Settings
from tests.api.auth.support import FakeSessions, browser, build_login_app, login_settings
from tests.identity.fake_providers import FakeProviders


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch) -> FakeSessions:
    fake_sessions = FakeSessions()
    monkeypatch.setattr(auth_login, "start_session", fake_sessions.start_session)
    monkeypatch.setattr(auth_login, "authenticate_session", fake_sessions.authenticate_session)
    monkeypatch.setattr(auth_login, "revoke_session", fake_sessions.revoke_session)
    return fake_sessions


@pytest.fixture
def login_app(db_settings: Settings, tmp_path: Path, fake: FakeProviders) -> FastAPI:
    return build_login_app(login_settings(db_settings, tmp_path), fake)


@pytest.fixture
def factory(login_app: FastAPI) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = login_app.state.session_factory
    return factory


@pytest.fixture
def client(login_app: FastAPI) -> Iterator[TestClient]:
    with browser(login_app) as client:
        yield client
