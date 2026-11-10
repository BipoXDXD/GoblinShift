"""`run.finished` gravado em memória, para os testes de quem conclui a Run (3.7)."""

from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.finished import SPAN_NAME, RunFinishedReporter

HASHER = UserHasher(b"chave-de-teste-do-user-hash-com-32-caracteres")


class RecordedFinished:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.reporter = RunFinishedReporter(factory, HASHER, self.provider.get_tracer("test"))

    def events(self) -> list[dict[str, Any]]:
        return [
            dict(span.attributes or {})
            for span in self.exporter.get_finished_spans()
            if span.name == SPAN_NAME
        ]

    def shutdown(self) -> None:
        self.provider.shutdown()
