"""Telemetria (docs/GoblinShiftPlan.md, 3.7): hash, IP truncado, erro do banco e spans de banco."""

import json
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from uuid import UUID

import pytest
import yaml
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError

from goblinshift.entrypoints.api import health
from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform import telemetry
from goblinshift.platform.settings import MissingSecretError, Settings
from goblinshift.platform.telemetry import (
    UserHasher,
    configure_tracing,
    make_span_processor,
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


# Fila do exportador da API


BSP_SETTINGS = ("OTEL_BSP_MAX_QUEUE_SIZE", "OTEL_BSP_MAX_EXPORT_BATCH_SIZE")
# Média medida com Postgres real: consultas e `connect` da requisição mais o evento amplo.
SPANS_PER_REQUEST = 13
STALLED_COLLECTOR_S = 30
REQUESTS_PER_S = 20


def _published_api_environment() -> dict[str, str]:
    app = json.loads((ROOT / "deploy" / "coolify" / "app.json").read_text(encoding="utf-8"))
    environment: dict[str, str] = app["services"]["api"]["environment"]
    return environment


@pytest.mark.parametrize("name", BSP_SETTINGS)
def test_compose_e_coolify_dao_a_mesma_fila_a_api(name: str) -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))

    assert compose["services"]["api"]["environment"][name] == _published_api_environment()[name]


class _GatedExporter(SpanExporter):
    """Coletor parado até o teste abrir a porta; conta os eventos amplos que recebe."""

    gate = threading.Event()
    received_main = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.gate.wait()
        _GatedExporter.received_main += sum(
            1 for span in spans if (span.attributes or {}).get("main") is True
        )
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.gate.set()


def test_fila_publicada_guarda_os_eventos_com_o_coletor_parado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = _published_api_environment()
    for name in BSP_SETTINGS:
        monkeypatch.setenv(name, published[name])
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://coletor.invalid")
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", _GatedExporter)
    monkeypatch.setattr(_GatedExporter, "gate", threading.Event())
    monkeypatch.setattr(_GatedExporter, "received_main", 0)
    processor = make_span_processor(None)
    assert processor is not None
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer(SQLALCHEMY_SCOPE)
    requests = STALLED_COLLECTOR_S * REQUESTS_PER_S

    for _ in range(requests):
        for _ in range(SPANS_PER_REQUEST - 1):
            tracer.start_span("SELECT goblinshift").end()
        tracer.start_span("POST /api/v1/runs", attributes={"main": True}).end()
    _GatedExporter.gate.set()
    provider.force_flush()
    provider.shutdown()

    assert _GatedExporter.received_main == requests
