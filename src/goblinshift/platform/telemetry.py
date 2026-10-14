"""Logs JSON e traces OpenTelemetry (docs/GoblinShiftPlan.md, 3.7).

Os traces só são exportados quando `OTEL_EXPORTER_OTLP_ENDPOINT` está definido; o
endpoint, o nome do serviço e os headers seguem as variáveis padrão do OpenTelemetry.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span
from sqlalchemy import Engine

from goblinshift.platform.settings import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Campos do evento estruturado: `logger.info("...", extra={"fields": {...}})`.
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


# Prefixos que sobram do IP do cliente no evento: o bastante para ver rede ou provedor, nunca a
# pessoa (3.7: "nunca entram em evento: ... IP completo").
IPV4_PREFIX_BITS = 24
IPV6_PREFIX_BITS = 48
UNKNOWN_ADDRESS = "unknown"
# Atributos que a instrumentação ASGI preenche com a URL completa, query string incluída, ou com
# o IP do cliente; nomes antigos e novos das convenções semânticas.
_URL_ATTRIBUTES = ("http.url", "url.full")
_TARGET_ATTRIBUTES = ("http.target",)
_QUERY_ATTRIBUTES = ("url.query",)
_CLIENT_ADDRESS_ATTRIBUTES = ("net.peer.ip", "client.address", "http.client_ip")


class UserHasher:
    """`user.hash` dos eventos: HMAC-SHA256 do `owner_id` com segredo do servidor (3.7).

    Agrupa eventos por conta sem guardar o ID: sem o segredo, o hash não volta ao `owner_id`.
    """

    def __init__(self, key: bytes) -> None:
        self._key = key

    def hash(self, owner_id: UUID) -> str:
        return hmac.new(self._key, owner_id.bytes, hashlib.sha256).hexdigest()


def truncate_client_address(address: str) -> str:
    """IP do cliente sem os bits que identificam a máquina; o que não é IP vira `unknown`."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return UNKNOWN_ADDRESS
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    bits = IPV4_PREFIX_BITS if isinstance(ip, ipaddress.IPv4Address) else IPV6_PREFIX_BITS
    return str(ipaddress.ip_network(f"{ip}/{bits}", strict=False).network_address)


def _start_main_event(span: Span, scope: dict[str, Any]) -> None:
    # Convenção "main = true": o span da requisição é o evento amplo dela. O resto dele vem do
    # middleware do evento amplo, no fim da requisição.
    if not span.is_recording():
        return
    span.set_attribute("main", True)
    _redact_request_attributes(span, scope)


def _redact_request_attributes(span: Span, scope: dict[str, Any]) -> None:
    """Tira a query string e o IP completo que a instrumentação ASGI pôs no span.

    A query carrega filtros e cursores, que são dados do usuário (3.7). Só sobrescreve os
    atributos que já existem, para não inventar nomes de outra versão das convenções.
    """
    present = getattr(span, "attributes", None) or {}
    path = str(scope.get("path", ""))
    url_without_query = f"{scope.get('scheme', 'http')}://{_host_of(scope)}{path}"
    client = scope.get("client")
    address = truncate_client_address(str(client[0])) if client else UNKNOWN_ADDRESS
    for names, value in (
        (_URL_ATTRIBUTES, url_without_query),
        (_TARGET_ATTRIBUTES, path),
        (_QUERY_ATTRIBUTES, ""),
        (_CLIENT_ADDRESS_ATTRIBUTES, address),
    ):
        for name in names:
            if name in present:
                span.set_attribute(name, value)


def _host_of(scope: dict[str, Any]) -> str:
    for name, value in scope.get("headers", []):
        if name == b"host":
            return bytes(value).decode("latin-1")
    server = scope.get("server")
    return f"{server[0]}:{server[1]}" if server else ""


def configure_tracing(
    app: FastAPI, engine: Engine, settings: Settings, exporter: SpanExporter | None = None
) -> None:
    """Liga os traces da API; sem `exporter` e sem endpoint OTLP, não faz nada.

    `exporter` serve aos testes, que leem os spans de um `InMemorySpanExporter`.
    """
    processor: SpanProcessor
    if exporter is not None:
        processor = SimpleSpanProcessor(exporter)
    elif os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        processor = BatchSpanProcessor(OTLPSpanExporter())
    else:
        return
    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "goblinshift-api"),
            "service.version": settings.service_version,
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(processor)
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls="livez,readyz",
        server_request_hook=_start_main_event,
        # Um span por pedaço enviado multiplicaria o volume num download por streaming; bytes e
        # tempos do envio já estão no evento amplo.
        exclude_spans=["receive", "send"],
    )
    SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)
