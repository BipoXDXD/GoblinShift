"""Spans do login sem `code`, `state` nem cookie (3.7; STRIDE da fatia 6, vazamento de token).

O `FastAPIInstrumentor` grava a URL do pedido no span, e no callback a query traz o `code` de
autorização e o `state`. Os spans saem para um `InMemorySpanExporter`, o mesmo caminho do
exportador OTLP de produção.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from goblinshift.platform.settings import Settings
from tests.api.auth.support import browser, build_login_app, login_settings, open_signup
from tests.api.test_wide_event import _span_text
from tests.identity.fake_providers import FakeProviders

pytestmark = pytest.mark.integration

CANARY = f"CANARY-{uuid.uuid4()}"


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    yield exporter
    # O instrumentor do SQLAlchemy é global: sem isso, os próximos testes exportariam aqui.
    SQLAlchemyInstrumentor().uninstrument()


@pytest.fixture
def traced_client(
    db_settings: Settings, tmp_path: Path, fake: FakeProviders, exporter: InMemorySpanExporter
) -> Iterator[TestClient]:
    app = build_login_app(login_settings(db_settings, tmp_path), fake, span_exporter=exporter)
    open_signup(app)
    with browser(app) as client:
        yield client
    app.state.engine.dispose()


def _all_span_text(exporter: InMemorySpanExporter) -> str:
    spans = exporter.get_finished_spans()
    assert spans, "nenhum span exportado: a instrumentação não está ligada no teste"
    return "\n".join(_span_text(span) for span in spans)


def test_code_e_state_do_login_real_nao_vao_ao_span(
    traced_client: TestClient, fake: FakeProviders, exporter: InMemorySpanExporter
) -> None:
    started = traced_client.get("/auth/login/google")
    callback = fake.consent(started.headers["location"])
    query = {name: values[0] for name, values in parse_qs(urlsplit(callback).query).items()}

    response = traced_client.get(callback)

    assert response.status_code == 303
    text = _all_span_text(exporter)
    assert "/auth/callback/google" in text
    assert query["code"] not in text
    assert query["state"] not in text
    assert traced_client.cookies["__Host-gs_session"] not in text


@pytest.mark.parametrize(
    "params",
    [
        {"code": CANARY, "state": CANARY},
        {"error": "access_denied", "error_description": CANARY, "state": CANARY},
    ],
    ids=["code-e-state", "erro-do-provedor"],
)
def test_canario_na_query_do_callback_nao_vai_ao_span(
    traced_client: TestClient, exporter: InMemorySpanExporter, params: dict[str, str]
) -> None:
    traced_client.get("/auth/login/google")

    response = traced_client.get("/auth/callback/google", params=params)

    assert response.status_code == 303
    assert "CANARY-" not in _all_span_text(exporter)
