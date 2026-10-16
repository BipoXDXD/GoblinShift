"""Documentação da API (Swagger UI e OpenAPI) fechada em produção (docs/GoblinShiftPlan.md, 3.6).

Fora de produção ela continua servida: os testes de contrato e o desenvolvimento a usam. O
contrato versionado (`gs-export-openapi`) sai de `app.openapi()`, que não depende da rota.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import Settings
from tests.test_settings_secrets import _production

DOC_PATHS = ("/docs", "/openapi.json")


@pytest.mark.parametrize("path", DOC_PATHS)
def test_producao_nao_serve_a_documentacao(tmp_path: Path, path: str) -> None:
    with TestClient(create_app(_production(tmp_path))) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "NOT_FOUND"
    assert b"swagger-ui" not in response.content.lower()
    assert b'"paths"' not in response.content


def test_producao_ainda_gera_o_contrato_sem_servi_lo(tmp_path: Path) -> None:
    app = create_app(_production(tmp_path))

    assert "/api/v1/me" in app.openapi()["paths"]


@pytest.mark.parametrize("environment", ["development", "test"])
@pytest.mark.parametrize("path", DOC_PATHS)
def test_fora_de_producao_a_documentacao_continua_servida(environment: str, path: str) -> None:
    settings = Settings.model_validate({"environment": environment, "db_port": 1})

    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 200
