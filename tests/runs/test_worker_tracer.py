"""Tracer do processo do worker (3.7): um provider só, e nenhum sem endpoint OTLP."""

import pytest
from opentelemetry.sdk.trace import Tracer as SdkTracer
from opentelemetry.trace import NoOpTracer

from goblinshift.entrypoints.worker import process_tracer
from goblinshift.platform.settings import Settings


def test_sem_endpoint_otlp_o_tracer_nao_grava(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    with process_tracer(Settings(environment="test"), "goblinshift-worker") as tracer:
        assert isinstance(tracer, NoOpTracer)


def test_com_endpoint_otlp_o_tracer_exporta(monkeypatch: pytest.MonkeyPatch) -> None:
    # Porta sem ninguém ouvindo: o lote vazio não chega a ser enviado no shutdown.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")

    with process_tracer(Settings(environment="test"), "goblinshift-worker") as tracer:
        assert isinstance(tracer, SdkTracer)
