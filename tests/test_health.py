import pytest
from fastapi.testclient import TestClient

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import Settings
from tests.conftest import run_migrations


def test_livez_responde_sem_banco() -> None:
    app = create_app(Settings(environment="test", db_port=1))
    with TestClient(app) as client:
        response = client.get("/livez")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_sem_banco_responde_503_sem_detalhes() -> None:
    app = create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


@pytest.mark.integration
def test_readyz_depende_das_migracoes(db_settings: Settings) -> None:
    app = create_app(db_settings)
    with TestClient(app) as client:
        run_migrations(db_settings, "base")
        assert client.get("/readyz").status_code == 503

        run_migrations(db_settings, "head")
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
