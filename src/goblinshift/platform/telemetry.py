"""Logs JSON e traces OpenTelemetry (docs/GoblinShiftPlan.md, 3.7).

Os traces só são exportados quando `OTEL_EXPORTER_OTLP_ENDPOINT` está definido; o
endpoint, o nome do serviço e os headers seguem as variáveis padrão do OpenTelemetry.
"""

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
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
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _mark_main_event(span: Span, scope: dict[str, Any]) -> None:
    # Convenção "main = true": o span da requisição é o evento amplo dela.
    if span.is_recording():
        span.set_attribute("main", True)


def configure_tracing(app: FastAPI, engine: Engine, settings: Settings) -> None:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "goblinshift-api"),
            "service.version": settings.service_version,
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls="livez,readyz",
        server_request_hook=_mark_main_event,
    )
    SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)
