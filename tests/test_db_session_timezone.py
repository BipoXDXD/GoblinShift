"""Sessão do banco em UTC: datas saem em UTC em toda rota, qualquer que seja o fuso do banco.

As rotas serializam o `datetime` que o driver devolve, com o fuso da sessão. O banco é posto em
São Paulo por `ALTER DATABASE … SET timezone`, o default que um servidor noutro fuso teria; a
opção de conexão de `make_engine` precisa vencer esse default.
"""

import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from goblinshift.entrypoints.api.app import create_app
from goblinshift.identity import CURRENT_TERMS_VERSION
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from tests.api.session_support import open_session, session_headers
from tests.api.test_terms import ACCEPT, ME, external_account
from tests.identity.support import prepare_database

pytestmark = pytest.mark.integration


@pytest.fixture
def engine_admin(db_settings: Settings) -> Iterator[Engine]:
    """Engine sem as `options` da aplicação, para mudar o default do banco."""
    engine = create_engine(db_settings.database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def banco_em_sao_paulo(db_settings: Settings, engine_admin: Engine) -> Iterator[None]:
    with engine_admin.begin() as conn:
        conn.exec_driver_sql(
            f"ALTER DATABASE \"{db_settings.db_name}\" SET timezone = 'America/Sao_Paulo'"
        )
    try:
        yield
    finally:
        with engine_admin.begin() as conn:
            conn.exec_driver_sql(f'ALTER DATABASE "{db_settings.db_name}" RESET timezone')


def test_sessao_da_aplicacao_fica_em_utc(db_settings: Settings, banco_em_sao_paulo: None) -> None:
    engine = make_engine(db_settings)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SHOW TimeZone").scalar_one() == "UTC"
            assert conn.exec_driver_sql("SELECT now()").scalar_one().utcoffset() == timedelta(0)
    finally:
        engine.dispose()


def test_aceite_dos_termos_sai_em_utc_com_o_banco_noutro_fuso(
    db_settings: Settings, banco_em_sao_paulo: None, tmp_path: Path
) -> None:
    # O app nasce depois da fixture: o pool só abre conexões com o banco já em São Paulo.
    app = create_app(db_settings.model_copy(update={"storage_root": tmp_path / "storage"}))
    try:
        prepare_database(db_settings, app.state.engine)
        owner = external_account(app.state.session_factory)
        with TestClient(app) as client:
            auth = session_headers(app, open_session(app, owner))
            accepted = client.post(ACCEPT, json={"version": CURRENT_TERMS_VERSION}, headers=auth)
            assert accepted.status_code == 200, accepted.text
            response = client.get(ME, headers=auth)
    finally:
        app.state.engine.dispose()

    assert response.status_code == 200, response.text
    body = response.json()
    assert re.search(r"(Z|\+00:00)$", body["terms"]["accepted_at"])
