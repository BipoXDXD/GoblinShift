"""Telemetria (docs/GoblinShiftPlan.md, 3.7): hash, IP truncado, erro do banco e spans de banco."""

import json
import logging
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import Mock
from uuid import UUID

import pytest
import sqlalchemy
import sqlalchemy.engine.create
import sqlalchemy.ext.asyncio
import wrapt
import yaml
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanKind, Status, StatusCode
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.event import listen as real_listen
from sqlalchemy.exc import OperationalError

from goblinshift.entrypoints import maintenance, worker
from goblinshift.entrypoints.api import health
from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform import telemetry
from goblinshift.platform.settings import MissingSecretError, Settings
from goblinshift.platform.telemetry import (
    UserHasher,
    configure_tracing,
    make_span_processor,
    make_tracer_provider,
    truncate_client_address,
)

ROOT = Path(__file__).resolve().parent.parent

OWNER = UUID("00000000-0000-0000-0000-000000000001")


def test_user_hash_e_o_hmac_sha256_do_owner_id_com_o_segredo() -> None:
    # Calculado à mão: hmac.new(b"k" * 32, OWNER.bytes, sha256).hexdigest()
    expected = "f10a472de832bb7dc5d2658295bfd945af2a2046c8e59f13c0af4a2cc8797800"

    assert UserHasher(b"k" * 32).hash(OWNER) == expected


def test_user_hash_muda_com_o_segredo() -> None:
    assert UserHasher(b"j" * 32).hash(OWNER) == (
        "c53dbfb8bee19c65767a352ea6b36550f153d30835616ea12082f103ab03bf73"
    )


def test_user_hash_nao_contem_o_owner_id() -> None:
    digest = UserHasher(b"k" * 32).hash(OWNER)

    assert str(OWNER) not in digest
    assert OWNER.hex not in digest


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("203.0.113.77", "203.0.113.0"),
        ("203.0.113.0", "203.0.113.0"),
        ("10.1.2.255", "10.1.2.0"),
        ("2001:db8:abcd:12:1:2:3:4", "2001:db8:abcd::"),
        ("::1", "::"),
        ("::ffff:203.0.113.77", "203.0.113.0"),
    ],
)
def test_ip_e_truncado_em_24_bits_no_ipv4_e_48_no_ipv6(address: str, expected: str) -> None:
    assert truncate_client_address(address) == expected


@pytest.mark.parametrize("address", ["testclient", "", "300.1.1.1", "203.0.113.77:443"])
def test_endereco_que_nao_e_ip_nao_vai_para_o_evento(address: str) -> None:
    assert truncate_client_address(address) == "unknown"


def test_producao_sem_segredo_do_user_hash_nao_sobe() -> None:
    settings = Settings(environment="production", db_port=1, cursor_secret=SecretStr("c" * 32))

    with pytest.raises(MissingSecretError, match="GS_USER_HASH_SECRET"):
        create_app(settings)


def test_producao_usa_o_segredo_do_user_hash_configurado() -> None:
    settings = Settings(environment="production", user_hash_secret=SecretStr("u" * 32))

    assert settings.user_hash_key() == b"u" * 32


@pytest.mark.parametrize("environment", ["development", "test"])
def test_desenvolvimento_e_teste_tem_segredo_padrao_do_user_hash(environment: str) -> None:
    settings = Settings.model_validate({"environment": environment})

    assert len(settings.user_hash_key()) >= 32


def test_segredo_do_user_hash_curto_e_recusado() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", user_hash_secret=SecretStr("u" * 31))


@pytest.mark.parametrize("service", ["api", "worker", "maintenance"])
def test_producao_passa_o_segredo_do_user_hash_a_quem_emite_eventos(service: str) -> None:
    # API (evento de requisição), worker e manutenção (término da Run) usam a mesma chave, para
    # que o mesmo `owner_id` dê o mesmo `user.hash` nos três.
    compose = yaml.safe_load((ROOT / "compose.prod.yaml").read_text())

    environment = compose["services"][service]["environment"]

    assert environment["GS_USER_HASH_SECRET"].startswith("${GS_USER_HASH_SECRET:?")


# Status de erro dos spans do SQLAlchemy


SQLALCHEMY_SCOPE = "opentelemetry.instrumentation.sqlalchemy"
REDACTED_DB_ERROR = "database error (detail redacted)"


def _export_one(scope: str, status: Status | None) -> ReadableSpan:
    """Termina um span do `scope` pelo processador de produção e devolve o que foi exportado."""
    exporter = InMemorySpanExporter()
    processor = make_span_processor(exporter)
    assert processor is not None
    provider = TracerProvider()
    provider.add_span_processor(processor)
    span = provider.get_tracer(scope).start_span("SELECT goblinshift")
    if status is not None:
        span.set_status(status)
    span.set_attribute("db.statement", "INSERT INTO t (k) VALUES (%(k)s)")
    span.end()
    (exported,) = exporter.get_finished_spans()
    return exported


def test_erro_do_banco_sai_sem_os_valores_do_detail() -> None:
    # A instrumentação grava `str(exceção)`, que no psycopg traz `DETAIL: Key (k)=(<valor>)`.
    canary = f"CANARY-{UUID(int=7)}"
    message = (
        f'duplicate key value violates unique constraint "t_pkey"\nDETAIL:  Key (k)=({canary})'
    )

    exported = _export_one(SQLALCHEMY_SCOPE, Status(StatusCode.ERROR, message))

    assert exported.status.status_code is StatusCode.ERROR
    assert exported.status.description == REDACTED_DB_ERROR
    assert exported.attributes == {"db.statement": "INSERT INTO t (k) VALUES (%(k)s)"}


def test_consulta_sem_erro_do_banco_sai_inalterada() -> None:
    exported = _export_one(SQLALCHEMY_SCOPE, None)

    assert exported.status.status_code is StatusCode.UNSET
    assert exported.status.description is None


def test_erro_de_outra_instrumentacao_mantem_a_descricao() -> None:
    exported = _export_one("opentelemetry.instrumentation.asgi", Status(StatusCode.ERROR, "x"))

    assert exported.status.description == "x"


def test_sem_exportador_nem_endpoint_nao_ha_processador(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    assert make_span_processor(None) is None


# Spans de banco com a instrumentação real da API, sobre SQLite para não depender de Docker


CANARY = f"CANARY-{UUID(int=11)}"
TEST_SETTINGS = Settings.model_validate({"environment": "test"})
REVISION = "rev-teste"
QUERY_ROUTE = "/consulta"


@dataclass(frozen=True)
class TracedApp:
    app: FastAPI
    engine: Engine
    exporter: InMemorySpanExporter

    def database_spans(self) -> list[ReadableSpan]:
        return [
            span
            for span in self.exporter.get_finished_spans()
            if span.instrumentation_scope is not None
            and span.instrumentation_scope.name == SQLALCHEMY_SCOPE
        ]

    def statements(self) -> list[str]:
        return [
            str(span.attributes["db.statement"])
            for span in self.database_spans()
            if span.attributes is not None and "db.statement" in span.attributes
        ]


@pytest.fixture
def traced_app(tmp_path: Path) -> Iterator[TracedApp]:
    # Arquivo, não `sqlite://`: as rotas síncronas rodam em outra thread e o banco em memória é
    # por conexão.
    engine = create_engine(f"sqlite:///{tmp_path / 'telemetria.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version VALUES (:r)"), {"r": REVISION})
        conn.execute(text("CREATE TABLE t (k VARCHAR(80) PRIMARY KEY)"))
    app = FastAPI()
    app.include_router(health.router)
    app.state.engine = engine
    app.state.code_heads = frozenset({REVISION})
    app.state.known_revisions = frozenset({REVISION})

    @app.get(QUERY_ROUTE)
    def query(request: Request) -> dict[str, int]:
        with request.app.state.engine.connect() as conn:
            return {"valor": conn.execute(text("SELECT 1")).scalar_one()}

    exporter = InMemorySpanExporter()
    configure_tracing(app, engine, Settings.model_validate({"environment": "test"}), exporter)
    try:
        yield TracedApp(app, engine, exporter)
    finally:
        # O instrumentor é global: sem isso, os próximos testes continuariam exportando aqui.
        SQLAlchemyInstrumentor().uninstrument()
        engine.dispose()


def test_consulta_gera_span_de_banco_no_sqlalchemy_em_uso(traced_app: TracedApp) -> None:
    with traced_app.engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    spans = [s for s in traced_app.database_spans() if s.name.startswith("SELECT")]
    assert [s.kind for s in spans] == [SpanKind.CLIENT]
    assert traced_app.statements() == ["SELECT 1"]
    assert spans[0].attributes is not None
    assert spans[0].attributes["db.system"] == "sqlite"


def test_parametros_da_consulta_nao_vao_ao_span(traced_app: TracedApp) -> None:
    with traced_app.engine.begin() as conn:
        conn.execute(text("INSERT INTO t (k) VALUES (:k)"), {"k": CANARY})
        conn.execute(text("SELECT k FROM t WHERE k = :k"), {"k": CANARY})

    assert traced_app.statements() == [
        "INSERT INTO t (k) VALUES (?)",
        "SELECT k FROM t WHERE k = ?",
    ]
    for span in traced_app.database_spans():
        assert CANARY not in span.to_json()


def test_erro_real_do_banco_sai_com_a_descricao_redigida(traced_app: TracedApp) -> None:
    with pytest.raises(OperationalError), traced_app.engine.connect() as conn:
        conn.execute(text("SELECT k FROM tabela_que_nao_existe WHERE k = :k"), {"k": CANARY})

    failed = [s for s in traced_app.database_spans() if s.status.status_code is StatusCode.ERROR]
    assert [s.status.description for s in failed] == [REDACTED_DB_ERROR]
    assert CANARY not in failed[0].to_json()


def test_span_de_banco_e_filho_da_requisicao_e_nao_conta_como_evento(
    traced_app: TracedApp,
) -> None:
    with TestClient(traced_app.app) as client:
        assert client.get(QUERY_ROUTE).status_code == 200

    spans = traced_app.exporter.get_finished_spans()
    (main,) = [s for s in spans if (s.attributes or {}).get("main") is True]
    database_spans = traced_app.database_spans()
    assert traced_app.statements() == ["SELECT 1"]
    for span in database_spans:
        assert span.parent is not None
        assert span.parent.span_id == main.context.span_id
        assert "main" not in (span.attributes or {})


def test_sonda_readyz_nao_gera_span_de_banco(traced_app: TracedApp) -> None:
    with TestClient(traced_app.app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert traced_app.exporter.get_finished_spans() == ()


# Eventos `exception` dos spans do SQLAlchemy: o banco fora do ar não pode expor a conexão


def _refuse_connection() -> NoReturn:
    raise sqlite3.OperationalError(f'connection to server at "{CANARY}", port 5432 failed')


def _failed_connect_spans(traced_app: TracedApp) -> list[ReadableSpan]:
    return [s for s in traced_app.database_spans() if s.name == "connect" and s.events]


def _otlp_payload(spans: Sequence[ReadableSpan]) -> bytes:
    """Os bytes que o exportador OTLP de produção enviaria."""
    payload: bytes = encode_spans(spans).SerializeToString()
    return payload


def test_excecao_do_connect_sai_redigida_no_export(traced_app: TracedApp) -> None:
    refused = create_engine("sqlite://", creator=_refuse_connection)

    with pytest.raises(OperationalError):
        refused.connect()

    (span,) = _failed_connect_spans(traced_app)
    (event,) = span.events
    assert event.name == "exception"
    assert event.attributes is not None
    assert event.attributes["exception.message"] == REDACTED_DB_ERROR
    assert event.attributes["exception.stacktrace"] == REDACTED_DB_ERROR
    assert event.attributes["exception.type"] == "sqlalchemy.exc.OperationalError"
    assert span.status.description == REDACTED_DB_ERROR
    payload = _otlp_payload([span])
    assert CANARY.encode() not in payload
    assert b"Traceback" not in payload


def test_postgres_recusado_nao_expoe_a_conexao_no_evento(traced_app: TracedApp) -> None:
    refused = create_engine("postgresql+psycopg://usuario:senha-secreta@127.0.0.1:1/banco")

    with pytest.raises(OperationalError):
        refused.connect()

    (span,) = _failed_connect_spans(traced_app)
    payload = _otlp_payload([span])
    assert b"senha-secreta" not in payload
    assert b"Connection refused" not in payload
    assert b"port 1" not in payload
    assert b"Traceback" not in payload


def test_evento_de_outra_instrumentacao_mantem_o_detalhe() -> None:
    exporter = InMemorySpanExporter()
    processor = make_span_processor(exporter)
    assert processor is not None
    provider = TracerProvider()
    provider.add_span_processor(processor)
    span = provider.get_tracer("opentelemetry.instrumentation.asgi").start_span("GET /x")

    span.record_exception(ValueError("detalhe"))
    span.end()

    (exported,) = exporter.get_finished_spans()
    assert exported.events[0].attributes is not None
    assert exported.events[0].attributes["exception.message"] == "detalhe"


# Boot da API quando a instrumentação do banco falha


# `sys.modules[...] = None` faz o import falhar como numa imagem sem o greenlet. Processo novo
# porque o SQLAlchemy só levanta esse erro no primeiro import de `sqlalchemy.ext.asyncio`.
BOOT_WITHOUT_GREENLET = """
import sys
sys.modules["greenlet"] = None
from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, text
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import configure_logging, configure_tracing

configure_logging()
engine = create_engine("sqlite://")
exporter = InMemorySpanExporter()
configure_tracing(FastAPI(), engine, Settings.model_validate({"environment": "test"}), exporter)
with engine.connect() as conn:
    assert conn.execute(text("SELECT 1")).scalar_one() == 1
print("SPANS", [span.name for span in exporter.get_finished_spans()])
"""


def test_falha_da_instrumentacao_nao_derruba_o_boot_e_fica_no_log() -> None:
    result = subprocess.run(  # noqa: S603 (só o próprio interpretador, com código fixo)
        [sys.executable, "-c", BOOT_WITHOUT_GREENLET],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "SPANS []" in result.stdout
    logged = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    assert [entry["level"] for entry in logged] == ["ERROR"]
    assert "spans de banco" in logged[0]["message"]
    assert "greenlet" in logged[0]["exception"]


# Falhas em qualquer ponto da instrumentação do banco


TRACER_LISTEN = "opentelemetry.instrumentation.sqlalchemy.engine.listen"


def _no_database_spans_after_failure(exporter: InMemorySpanExporter, engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
    engine.dispose()
    assert exporter.get_finished_spans() == ()


def test_qualquer_excecao_do_instrumentor_fica_no_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(_self: SQLAlchemyInstrumentor, **_kwargs: object) -> NoReturn:
        raise RuntimeError("instrumentor quebrado")

    monkeypatch.setattr(SQLAlchemyInstrumentor, "instrument", fail)
    engine = create_engine("sqlite://")
    exporter = InMemorySpanExporter()

    with caplog.at_level(logging.ERROR):
        configure_tracing(FastAPI(), engine, TEST_SETTINGS, exporter)

    assert "instrumentação do banco falhou" in caplog.text
    _no_database_spans_after_failure(exporter, engine)


def test_falha_no_meio_dos_listeners_desfaz_tudo(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = iter(range(10))

    def listen_failing_on_third(*args: Any, **kwargs: Any) -> None:
        if next(calls) == 2:
            raise RuntimeError("terceiro listener")
        real_listen(*args, **kwargs)

    monkeypatch.setattr(TRACER_LISTEN, listen_failing_on_third)
    engine = create_engine("sqlite://")
    exporter = InMemorySpanExporter()

    configure_tracing(FastAPI(), engine, TEST_SETTINGS, exporter)

    for wrapped in (
        sqlalchemy.create_engine,
        sqlalchemy.engine.create_engine,
        sqlalchemy.engine.create.create_engine,
        sqlalchemy.ext.asyncio.create_async_engine,
        Engine.connect,
    ):
        assert not isinstance(wrapped, wrapt.BaseObjectProxy)
    _no_database_spans_after_failure(exporter, engine)


# A cópia redigida preserva o resto do span


def test_copia_redigida_preserva_identidade_e_hierarquia() -> None:
    exporter = InMemorySpanExporter()
    processor = make_span_processor(exporter)
    assert processor is not None
    resource = Resource.create({"service.name": "copia"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(processor)
    tracer = provider.get_tracer(SQLALCHEMY_SCOPE)
    linked = tracer.start_span("ligado")

    with tracer.start_as_current_span("pai") as parent:
        span = tracer.start_span(
            "SELECT goblinshift",
            kind=SpanKind.CLIENT,
            attributes={"db.system": "postgresql"},
            links=[Link(linked.get_span_context())],
            start_time=1_000,
        )
        span.set_status(Status(StatusCode.ERROR, "detalhe"))
        span.end(end_time=2_000)

    exported = next(s for s in exporter.get_finished_spans() if s.name == "SELECT goblinshift")
    assert exported.status.description == REDACTED_DB_ERROR
    assert exported.parent is not None
    assert exported.parent.span_id == parent.get_span_context().span_id
    assert exported.context is not None
    assert exported.context.span_id == span.get_span_context().span_id
    assert exported.kind is SpanKind.CLIENT
    assert (exported.start_time, exported.end_time) == (1_000, 2_000)
    assert exported.attributes == {"db.system": "postgresql"}
    assert [link.context.span_id for link in exported.links] == [linked.get_span_context().span_id]
    assert exported.resource.attributes["service.name"] == "copia"
    assert exported.instrumentation_scope is not None
    assert exported.instrumentation_scope.name == SQLALCHEMY_SCOPE


# Caminho de produção: `BatchSpanProcessor` com o exportador OTLP, sem `exporter` injetado


# Média medida com Postgres real: consultas e `connect` da requisição mais o evento amplo.
SPANS_PER_REQUEST = 13
STALLED_COLLECTOR_S = 30
REQUESTS_PER_S = 20
API_QUEUE_SIZE = 8192
API_BATCH_SIZE = 2048


class _GatedExporter(SpanExporter):
    """Coletor que só recebe depois que o teste abre a porta; guarda os lotes recebidos."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.batches: list[list[ReadableSpan]] = []

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.gate.wait()
        self.batches.append(list(spans))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.gate.set()

    def spans(self) -> list[ReadableSpan]:
        return [span for batch in self.batches for span in batch]


@dataclass(frozen=True)
class ProductionTracing:
    provider: TracerProvider
    exporter: _GatedExporter


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch) -> Iterator[ProductionTracing]:
    exporter = _GatedExporter()
    providers: list[TracerProvider] = []
    real_make_tracer_provider = telemetry.make_tracer_provider

    def keep_provider(*args: Any, **kwargs: Any) -> TracerProvider | None:
        provider = real_make_tracer_provider(*args, **kwargs)
        assert provider is not None
        providers.append(provider)
        return provider

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://coletor.invalid")
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda: exporter)
    monkeypatch.setattr(telemetry, "make_tracer_provider", keep_provider)
    engine = create_engine("sqlite://")
    configure_tracing(FastAPI(), engine, TEST_SETTINGS)
    try:
        yield ProductionTracing(providers[0], exporter)
    finally:
        exporter.gate.set()
        SQLAlchemyInstrumentor().uninstrument()
        providers[0].shutdown()
        engine.dispose()


def test_redacao_vale_no_exportador_de_producao(production: ProductionTracing) -> None:
    production.exporter.gate.set()
    refused = create_engine("sqlite://", creator=_refuse_connection)

    with pytest.raises(OperationalError):
        refused.connect()
    production.provider.force_flush()

    spans = production.exporter.spans()
    (span,) = [s for s in spans if s.name == "connect" and s.events]
    assert span.status.description == REDACTED_DB_ERROR
    assert span.events[0].attributes is not None
    assert span.events[0].attributes["exception.message"] == REDACTED_DB_ERROR
    payload = _otlp_payload(spans)
    assert CANARY.encode() not in payload
    assert b"Traceback" not in payload


def test_fila_da_api_guarda_os_eventos_e_exporta_em_lotes_de_2048(
    production: ProductionTracing,
) -> None:
    tracer = production.provider.get_tracer(SQLALCHEMY_SCOPE)
    requests = STALLED_COLLECTOR_S * REQUESTS_PER_S

    for _ in range(requests):
        for _ in range(SPANS_PER_REQUEST - 1):
            tracer.start_span("SELECT goblinshift").end()
        tracer.start_span("POST /api/v1/runs", attributes={"main": True}).end()
    production.exporter.gate.set()
    production.provider.force_flush()

    received = [s for s in production.exporter.spans() if (s.attributes or {}).get("main") is True]
    assert len(received) == requests
    assert max(len(batch) for batch in production.exporter.batches) == API_BATCH_SIZE


def _record_queue_limits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Troca o `BatchSpanProcessor` por um registro dos limites que cada processo escolheu."""
    recorded: list[dict[str, Any]] = []

    def record(exporter: SpanExporter, **limits: Any) -> SimpleSpanProcessor:
        recorded.append(limits)
        return SimpleSpanProcessor(exporter)

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://coletor.invalid")
    monkeypatch.setattr(telemetry, "BatchSpanProcessor", record)
    return recorded


def test_api_escolhe_a_fila_maior_na_imagem(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _record_queue_limits(monkeypatch)
    engine = create_engine("sqlite://")

    try:
        configure_tracing(FastAPI(), engine, TEST_SETTINGS)
    finally:
        SQLAlchemyInstrumentor().uninstrument()
        engine.dispose()

    assert recorded == [{"max_queue_size": API_QUEUE_SIZE, "max_export_batch_size": API_BATCH_SIZE}]


def test_sem_escolha_a_fila_fica_no_padrao_do_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = _record_queue_limits(monkeypatch)

    provider = make_tracer_provider(TEST_SETTINGS, "goblinshift-worker")

    assert provider is not None
    provider.shutdown()
    assert recorded == [{"max_queue_size": None, "max_export_batch_size": None}]


def test_worker_nao_escolhe_a_fila_da_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def no_provider(*_args: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(worker, "make_tracer_provider", no_provider)

    with worker.process_tracer(TEST_SETTINGS, "goblinshift-worker"):
        pass

    assert calls == [{}]


def test_manutencao_nao_escolhe_a_fila_da_api(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class ProviderRequestedError(Exception):
        pass

    def stop_at_provider(*_args: object, **kwargs: object) -> NoReturn:
        calls.append(kwargs)
        raise ProviderRequestedError

    monkeypatch.setattr(maintenance, "make_tracer_provider", stop_at_provider)

    with pytest.raises(ProviderRequestedError):
        maintenance.serve(TEST_SETTINGS, Mock(), threading.Event())

    assert calls == [{}]
