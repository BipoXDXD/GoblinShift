"""Exportadores falsos dos testes de encerramento do tracing (API, worker e manutenção)."""

import threading
from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Bem acima da duração do teste: o lote só sai pelo encerramento, nunca pelo relógio do SDK.
BATCH_DELAY_MS = "600000"
# Curto para o teste do prazo estourado não esperar o prazo real.
SHORT_SHUTDOWN_TIMEOUT_S = 0.05


class StuckExporter(SpanExporter):
    """Collector que não responde: a exportação só volta quando o teste solta a trava."""

    def __init__(self) -> None:
        self.gate = threading.Event()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.gate.wait()
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


class BrokenShutdownExporter(InMemorySpanExporter):
    def shutdown(self) -> None:
        raise RuntimeError("falha no encerramento do exportador")
