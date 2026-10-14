"""Evento amplo da requisição: o span `main = true` (docs/GoblinShiftPlan.md, 3.7).

Os spans saem para um `InMemorySpanExporter`, o mesmo caminho do exportador OTLP de produção.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.dependencies import current_time
from goblinshift.entrypoints.api.wide_event import RateLimitClass, rate_limit_class
from goblinshift.files.storage import FileStorage
from goblinshift.identity import Scope
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import UserHasher
from openobserve.catalog import slo_named
from tests.api.files_support import bearer, new_account, publish
from tests.api.support import add_test_routes
from tests.identity.support import new_key, new_user, prepare_database
from tests.runs.support import ready_inputs

pytestmark = pytest.mark.integration

ECHO = "/api/v1/_test/echo"
RUNS = "/api/v1/runs"
CLIENT_ADDRESS = ("203.0.113.77", 51234)
FROZEN_NOW = datetime(2026, 10, 14, 12, 0, 15, tzinfo=UTC)


class Traced:
    def __init__(self, app: FastAPI, client: TestClient, exporter: InMemorySpanExporter) -> None:
        self.app = app
        self.client = client
        self.exporter = exporter

    @property
    def factory(self) -> sessionmaker[Session]:
        factory: sessionmaker[Session] = self.app.state.session_factory
        return factory

    def main_event(self) -> dict[str, Any]:
        """Atributos do único evento principal exportado desde a última limpeza."""
        mains = [span for span in self.spans() if (span.attributes or {}).get("main") is True]
        assert len(mains) == 1, [span.name for span in self.spans()]
        return dict(mains[0].attributes or {})

    def spans(self) -> tuple[ReadableSpan, ...]:
        return self.exporter.get_finished_spans()

    def clear(self) -> None:
        self.exporter.clear()


@pytest.fixture
def traced(db_settings: Settings, tmp_path: Path) -> Iterator[Traced]:
    exporter = InMemorySpanExporter()
    settings = db_settings.model_copy(update={"storage_root": tmp_path / "storage"})
    app = create_app(settings, span_exporter=exporter)
    add_test_routes(app)
    prepare_database(db_settings, app.state.engine)
    try:
        with TestClient(app, client=CLIENT_ADDRESS) as client:
            yield Traced(app, client, exporter)
    finally:
        # O instrumentor do SQLAlchemy é global: sem isso, os engines dos próximos testes
        # continuariam mandando spans para este exportador.
        SQLAlchemyInstrumentor().uninstrument()


# Atributos de toda requisição


def test_evento_de_requisicao_autenticada_tem_conta_credencial_e_consultas(
    traced: Traced,
) -> None:
    user_id, auth = new_account(traced.factory)
    traced.clear()

    response = traced.client.get("/api/v1/me", headers=auth)

    assert response.status_code == 200
    event = traced.main_event()
    assert event["http.route"] == "/api/v1/me"
    assert event["auth.via"] == "api_key"
    assert event["user.hash"] == UserHasher(traced.app.state.settings.user_hash_key()).hash(user_id)
    assert event["request_id"] == response.headers["x-request-id"]
    assert event["rate_limit.class"] == "read"
    assert "error.code" not in event
    # Autenticação e leitura da conta: pelo menos duas consultas.
    assert event["db.query.count"] >= 2
    assert event["db.query.duration_ms"] >= 0
    assert event["duration_ms"] > 0


def test_evento_nunca_traz_o_owner_id(traced: Traced) -> None:
    user_id, auth = new_account(traced.factory)
    traced.clear()

    traced.client.get("/api/v1/me", headers=auth)

    for span in traced.spans():
        assert str(user_id) not in _span_text(span)
        assert user_id.hex not in _span_text(span)


def test_evento_conta_os_bytes_recebidos_e_enviados(traced: Traced) -> None:
    payload = json.dumps({"text": "abcdef"}).encode()

    response = traced.client.post(
        ECHO, content=payload, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 200
    event = traced.main_event()
    assert event["http.request.body.size"] == len(payload)
    assert event["http.response.body.size"] == len(response.content)
    assert event["http.response.completed"] is True
    assert 0 <= event["http.response.first_byte_ms"] <= event["duration_ms"]


def test_requisicao_sem_credencial_nao_tem_conta_e_traz_o_codigo_de_erro(traced: Traced) -> None:
    response = traced.client.get("/api/v1/me")

    assert response.status_code == 401
    event = traced.main_event()
    assert event["error.code"] == "AUTHENTICATION_REQUIRED"
    assert "user.hash" not in event
    assert "auth.via" not in event


def test_escopo_insuficiente_traz_a_conta_e_o_codigo(traced: Traced) -> None:
    user_id = new_user(traced.factory)
    key = new_key(traced.factory, user_id, scopes=frozenset({Scope.RUNS_READ}))
    traced.clear()

    response = traced.client.get("/api/v1/_test/files-write", headers=bearer(key.token))

    assert response.status_code == 403
    event = traced.main_event()
    assert event["error.code"] == "INSUFFICIENT_SCOPE"
    assert event["auth.via"] == "api_key"
    assert "user.hash" in event


def test_rota_inexistente_traz_not_found_sem_classe_de_taxa(traced: Traced) -> None:
    traced.client.get("/api/v1/nada-aqui")

    event = traced.main_event()
    assert event["error.code"] == "NOT_FOUND"
    assert "rate_limit.class" not in event


def test_validacao_traz_validation_failed(traced: Traced) -> None:
    traced.client.post(ECHO, json={"text": 1})

    assert traced.main_event()["error.code"] == "VALIDATION_FAILED"


def test_excecao_inesperada_traz_internal_error(traced: Traced) -> None:
    response = traced.client.get("/api/v1/_test/boom")

    assert response.status_code == 500
    assert traced.main_event()["error.code"] == "INTERNAL_ERROR"


def test_stream_interrompido_traz_internal_error_e_nao_concluido(traced: Traced) -> None:
    with pytest.raises(Exception, match="disco sumiu"):
        traced.client.get("/api/v1/_test/stream-boom")

    event = traced.main_event()
    assert event["error.code"] == "INTERNAL_ERROR"
    assert event["http.response.completed"] is False
    assert event["http.response.body.size"] == len(b"primeiro pedaco")


def test_ip_do_cliente_vai_truncado(traced: Traced) -> None:
    traced.client.get("/api/v1/me")

    for span in traced.spans():
        assert CLIENT_ADDRESS[0] not in _span_text(span)
    event = traced.main_event()
    addresses = {event.get("client.address"), event.get("net.peer.ip")} - {None}
    assert addresses == {"203.0.113.0"}


def test_consulta_que_falha_entra_na_contagem_e_nao_deixa_inicio_pendente(traced: Traced) -> None:
    response = traced.client.get("/api/v1/_test/failed-query")

    assert response.json() == {"pending_query_starts": 0}
    assert traced.main_event()["db.query.count"] == 1


def test_sem_spans_de_send_e_receive(traced: Traced) -> None:
    traced.client.post(ECHO, json={"text": "x"})

    assert len(traced.spans()) == 1


# POST /runs: aceitação e replay


def test_aceitacao_traz_run_receita_e_replay(traced: Traced, tmp_path: Path) -> None:
    user_id, auth = new_account(traced.factory)
    storage = FileStorage(tmp_path / "storage")
    inputs = ready_inputs(traced.factory, storage, user_id)
    payload = {
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "input_upload_ids": [str(i) for i in inputs],
        "parameters": {},
    }
    headers = {**auth, "Idempotency-Key": str(uuid.uuid4())}
    traced.clear()

    first = traced.client.post(RUNS, json=payload, headers=headers)

    assert first.status_code == 202, first.text
    event = traced.main_event()
    assert event["rate_limit.class"] == "run_create"
    assert event["idempotency.replayed"] is False
    assert event["run_id"] == first.json()["id"]
    assert event["recipe_id"] == "csv-janitor"
    assert event["recipe_version"] == 1
    traced.clear()

    again = traced.client.post(RUNS, json=payload, headers=headers)

    assert again.status_code == 202
    assert traced.main_event()["idempotency.replayed"] is True


# Limite de requisições (1.7; ADR 028)


def test_requisicao_atendida_traz_rate_limit_limited_falso(traced: Traced) -> None:
    _, auth = new_account(traced.factory)
    traced.clear()

    traced.client.get("/api/v1/me", headers=auth)

    assert traced.main_event()["rate_limit.limited"] is False


def test_requisicao_recusada_pelo_limite_traz_rate_limit_limited_e_o_codigo(
    traced: Traced,
) -> None:
    traced.app.dependency_overrides[current_time] = lambda: FROZEN_NOW
    _, auth = new_account(traced.factory)
    headers = {**auth, "Idempotency-Key": str(uuid.uuid4())}
    for _ in range(10):
        traced.client.post(f"{RUNS}?validate_only=true", json={}, headers=headers)
    traced.clear()

    response = traced.client.post(f"{RUNS}?validate_only=true", json={}, headers=headers)

    assert response.status_code == 429
    event = traced.main_event()
    assert event["rate_limit.limited"] is True
    assert event["rate_limit.class"] == "run_create"
    assert event["error.code"] == "RATE_LIMITED"


def test_requisicao_sem_credencial_nao_traz_rate_limit_limited(traced: Traced) -> None:
    traced.client.get("/api/v1/me")

    assert "rate_limit.limited" not in traced.main_event()


# Canário: nada do que o cliente manda como segredo ou dado chega ao span


def test_canario_em_credencial_cookie_agente_chave_query_nome_e_parametro_nunca_vai_ao_span(
    traced: Traced,
) -> None:
    canary = f"CANARY-{uuid.uuid4()}"
    _, auth = new_account(traced.factory)
    traced.clear()

    traced.client.get(
        f"/api/v1/runs?recipe_id={canary}",
        headers={
            "Authorization": f"Bearer {canary}",
            "Cookie": f"session={canary}",
            "User-Agent": canary,
        },
    )
    traced.client.get(f"/api/v1/runs?recipe_id={canary}&cursor={canary}", headers=auth)
    traced.client.post(
        "/api/v1/uploads",
        json={"items": [{"name": f"{canary}.csv", "size": 3}]},
        headers={**auth, "X-Api-Key": canary},
    )
    traced.client.post(
        RUNS,
        json={
            "recipe_id": "csv-janitor",
            "recipe_version": 1,
            "input_upload_ids": [str(uuid.uuid4())],
            "parameters": {"filters": [{"column": canary, "equals": canary}]},
        },
        headers={**auth, "Idempotency-Key": canary},
    )
    traced.client.get("/api/v1/me", headers=bearer(f"gs_{canary}"))

    spans = traced.spans()
    assert len([s for s in spans if (s.attributes or {}).get("main") is True]) == 5
    for span in spans:
        assert "CANARY-" not in _span_text(span), span.name


# Classe de taxa (1.7)


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/v1/runs", RateLimitClass.READ),
        ("HEAD", "/api/v1/runs/{run_id}", RateLimitClass.READ),
        ("POST", "/api/v1/runs", RateLimitClass.RUN_CREATE),
        ("POST", "/api/v1/runs/{run_id}/cancel", RateLimitClass.COMMAND),
        ("POST", "/api/v1/uploads", RateLimitClass.COMMAND),
        ("DELETE", "/api/v1/uploads/{upload_id}", RateLimitClass.COMMAND),
        ("PUT", "/api/v1/uploads/{upload_id}/content", RateLimitClass.TRANSFER),
        ("GET", "/api/v1/artifacts/{artifact_id}/download", RateLimitClass.TRANSFER),
    ],
)
def test_classe_de_taxa_por_metodo_e_rota(method: str, path: str, expected: RateLimitClass) -> None:
    assert rate_limit_class(method, path) is expected


def test_rotas_com_classe_explicita_existem_na_aplicacao(traced: Traced) -> None:
    paths = traced.app.openapi()["paths"]
    routes = {(method.upper(), path) for path, item in paths.items() for method in item}

    assert ("PUT", "/api/v1/uploads/{upload_id}/content") in routes
    assert ("GET", "/api/v1/artifacts/{artifact_id}/download") in routes
    assert ("POST", "/api/v1/runs") in routes


def _span_text(span: ReadableSpan) -> str:
    """Tudo o que o exportador manda do span, como texto, para a busca do canário."""
    parts: list[str] = [span.name, str(span.status.description or "")]
    parts += [f"{key}={value!r}" for key, value in (span.attributes or {}).items()]
    for event in span.events:
        parts.append(event.name)
        parts += [f"{key}={value!r}" for key, value in (event.attributes or {}).items()]
    return "\n".join(parts)


# Contrato com os SLOs: as consultas de deploy/openobserve leem estes atributos


def _openobserve_columns(event: dict[str, Any]) -> set[str]:
    """Nomes das colunas no OpenObserve, que troca "." por "_" nos atributos."""
    return {name.replace(".", "_") for name in event}


def test_eventos_de_post_runs_trazem_as_colunas_do_slo_de_aceitacao(
    traced: Traced, tmp_path: Path
) -> None:
    user_id, auth = new_account(traced.factory)
    inputs = ready_inputs(traced.factory, FileStorage(tmp_path / "storage"), user_id)
    payload = {
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "input_upload_ids": [str(i) for i in inputs],
        "parameters": {},
    }
    traced.clear()
    accepted = traced.client.post(
        RUNS, json=payload, headers={**auth, "Idempotency-Key": str(uuid.uuid4())}
    )
    accepted_event = traced.main_event()
    traced.clear()
    refused = traced.client.post(
        RUNS, json={"recipe_id": "nao-existe"}, headers={**auth, "Idempotency-Key": "k" * 16}
    )
    refused_event = traced.main_event()

    assert (accepted.status_code, refused.status_code) == (202, 422)
    columns = _openobserve_columns(accepted_event) | _openobserve_columns(refused_event)
    assert set(slo_named("aceitacao").columns) <= columns
    assert accepted_event["http.route"] == "/api/v1/runs"
    assert accepted_event["http.method"] == "POST"


def test_evento_de_download_traz_as_colunas_do_slo_de_download(traced: Traced) -> None:
    owner, auth = new_account(traced.factory)
    artifact_id = publish(traced.app, owner, b"a,b\n1,2\n")
    traced.clear()

    response = traced.client.get(f"/api/v1/artifacts/{artifact_id}/download", headers=auth)

    assert response.status_code == 200
    event = traced.main_event()
    assert set(slo_named("download").columns) <= _openobserve_columns(event)
    assert event["http.route"] == "/api/v1/artifacts/{artifact_id}/download"
