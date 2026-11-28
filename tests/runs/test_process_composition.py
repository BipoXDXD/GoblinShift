"""Composição dos processos: a ordem dos efeitos na subida e na parada (auditoria, achado 7).

Caracterização: fixa o comportamento de hoje da API, do `gs-worker`, do `gs-maintenance` e do
emissor do `run.finished` do `gs-admin`, para a composição poder mudar de módulo sem mudar a ordem.
Os pontos de troca são os que não mudam com o módulo: o `OTLPSpanExporter` da telemetria (abrir e
fechar o tracing), os segredos lidos em `Settings` e os nomes que cada processo usa no próprio
módulo. Sem banco: o engine aponta para a porta 1 e nunca conecta.
"""

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Self
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints import admin_cli, maintenance, worker
from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.maintenance import Cycle
from goblinshift.entrypoints.shutdown import ShutdownFlag
from goblinshift.platform import telemetry
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.finished import RunFinishedReporter
from tests.span_exporters import BATCH_DELAY_MS

# Escopo dos spans de worker, manutenção e `gs-admin` desde sempre.
PROCESS_SCOPE = "goblinshift.entrypoints.worker"
PROBE_SPAN = "sonda da composição"


class Events:
    """Linha do tempo dos efeitos da subida e da parada, na ordem em que aconteceram."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.exporters: list[InMemorySpanExporter] = []

    def add(self, event: str) -> None:
        self.seen.append(event)

    def exporter(self) -> InMemorySpanExporter:
        """Fábrica do `OTLPSpanExporter`: criar é abrir o tracing; `shutdown` é fechá-lo."""
        events = self

        class _Recording(InMemorySpanExporter):
            def shutdown(self) -> None:
                events.add("tracing fecha")
                super().shutdown()

        self.add("tracing abre")
        created = _Recording()
        self.exporters.append(created)
        return created

    def scopes(self, name: str) -> list[str | None]:
        return [
            span.instrumentation_scope.name if span.instrumentation_scope else None
            for exporter in self.exporters
            for span in exporter.get_finished_spans()
            if span.name == name
        ]

    def engine_maker(self) -> Callable[[Settings], Engine]:
        """`make_engine` de verdade (não conecta), com a criação e o `dispose` na linha do tempo."""

        def make(settings: Settings) -> Engine:
            self.add("engine cria")
            engine = make_engine(settings)
            dispose = engine.dispose

            def recorded_dispose(close: bool = True) -> None:
                self.add("engine dispose")
                dispose(close)

            engine.dispose = recorded_dispose  # type: ignore[method-assign]
            return engine

        return make

    def reporter_maker(self) -> Callable[..., RunFinishedReporter]:
        def make(
            factory: sessionmaker[Session], hasher: UserHasher, tracer: Tracer
        ) -> RunFinishedReporter:
            self.add("reporter")
            return RunFinishedReporter(factory, hasher, tracer)

        return make


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> Iterator[Events]:
    timeline = Events()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://coletor.invalid")
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", BATCH_DELAY_MS)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", timeline.exporter)
    user_hash_key = Settings.user_hash_key
    database_password = Settings.database_password

    def recorded_user_hash_key(self: Settings) -> bytes:
        timeline.add("segredo user.hash")
        return user_hash_key(self)

    def recorded_database_password(self: Settings) -> str:
        timeline.add("segredo do banco")
        return database_password(self)

    monkeypatch.setattr(Settings, "user_hash_key", recorded_user_hash_key)
    monkeypatch.setattr(Settings, "database_password", recorded_database_password)
    try:
        yield timeline
    finally:
        SQLAlchemyInstrumentor().uninstrument()


def _settings(tmp_path: Path) -> Settings:
    return Settings(environment="test", db_port=1, storage_root=tmp_path / "storage")


# Subida dos `main` ------------------------------------------------------------------------------


@pytest.mark.parametrize("process", [worker, maintenance], ids=["worker", "manutenção"])
def test_main_monta_o_runner_antes_dos_segredos_e_serve_por_ultimo(
    process: Any, events: Events, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    runner = object()

    def build_runner(received: Settings) -> object:
        assert received is settings
        events.add("runner")
        return runner

    def serve(received: Settings, built: object, _shutdown: ShutdownFlag) -> None:
        assert (received, built) == (settings, runner)
        events.add("serve")

    monkeypatch.setattr(process, "configure_logging", lambda: events.add("logging"))
    monkeypatch.setattr(process, "load_settings_or_exit", lambda _logger: settings)
    monkeypatch.setattr(process, "build_runner", build_runner)
    monkeypatch.setattr(process, "shutdown_on_signals", ShutdownFlag)
    monkeypatch.setattr(process, "serve", serve)

    assert process.main() == process.EXIT_OK
    assert events.seen == [
        "logging",
        "runner",
        "segredo user.hash",
        "segredo do banco",
        "serve",
    ]


# Worker -----------------------------------------------------------------------------------------


def test_worker_abre_e_fecha_na_ordem_de_sempre(
    events: Events, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shutdown = ShutdownFlag()
    tracers: list[Tracer] = []

    class Listener:
        def __init__(self, _engine: object, _connect_timeout_s: int) -> None:
            events.add("listen abre")

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            events.add("listen fecha")

        def wait(self, _timeout_s: float) -> bool:
            return False

    def reporter(
        factory: sessionmaker[Session], hasher: UserHasher, tracer: Tracer
    ) -> RunFinishedReporter:
        tracers.append(tracer)
        return events.reporter_maker()(factory, hasher, tracer)

    def one_turn(*_args: object) -> bool:
        events.add("volta")
        tracers[0].start_span(PROBE_SPAN).end()
        shutdown.request()
        return True

    monkeypatch.setattr(worker, "make_engine", events.engine_maker())
    monkeypatch.setattr(worker, "QueueListener", Listener)
    monkeypatch.setattr(worker, "RunFinishedReporter", reporter)
    monkeypatch.setattr(worker, "_work_once", one_turn)

    worker.serve(_settings(tmp_path), Mock(), shutdown)

    assert events.seen == [
        "engine cria",
        "segredo do banco",
        "segredo user.hash",
        "tracing abre",
        "listen abre",
        "reporter",
        "volta",
        "listen fecha",
        "engine dispose",
        "tracing fecha",
    ]
    assert events.scopes(PROBE_SPAN) == [PROCESS_SCOPE]


# Manutenção -------------------------------------------------------------------------------------


def test_manutencao_abre_e_fecha_na_ordem_de_sempre(
    events: Events, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shutdown = ShutdownFlag()

    def one_turn(
        tracer: Tracer,
        cycles: Sequence[Cycle],
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        events.add("volta")
        assert [name for name, _cycle in cycles] == ["execuções", "contas"]
        assert should_stop() is False
        tracer.start_span(PROBE_SPAN).end()
        shutdown.request()

    monkeypatch.setattr(maintenance, "make_engine", events.engine_maker())
    monkeypatch.setattr(maintenance, "RunFinishedReporter", events.reporter_maker())
    monkeypatch.setattr(maintenance, "run_cycles", one_turn)

    maintenance.serve(_settings(tmp_path), Mock(), shutdown)

    assert events.seen == [
        "tracing abre",
        "engine cria",
        "segredo do banco",
        "segredo user.hash",
        "reporter",
        "volta",
        "engine dispose",
        "tracing fecha",
    ]
    assert events.scopes(PROBE_SPAN) == [PROCESS_SCOPE]


# gs-admin ---------------------------------------------------------------------------------------


def test_emissor_do_admin_le_o_segredo_antes_de_abrir_o_tracing(
    events: Events, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    factory: sessionmaker[Session] = sessionmaker()
    context = admin_cli._Context(factory=factory, settings=settings, clock=lambda: 0.0)

    with admin_cli._run_finished_reporter(context) as finished:
        events.add("comando")
        assert isinstance(finished, RunFinishedReporter)
        finished_tracer: Tracer = finished._tracer
        finished_tracer.start_span(PROBE_SPAN).end()

    assert events.seen == ["segredo user.hash", "tracing abre", "comando", "tracing fecha"]
    assert events.scopes(PROBE_SPAN) == [PROCESS_SCOPE]


# API --------------------------------------------------------------------------------------------


def test_api_libera_o_banco_antes_de_fechar_o_tracing(
    events: Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))
    engine = app.state.engine
    dispose = engine.dispose

    def recorded_dispose() -> None:
        events.add("engine dispose")
        dispose()

    monkeypatch.setattr(engine, "dispose", recorded_dispose)

    with TestClient(app) as client:
        events.add("serve")
        assert client.get("/openapi.json").status_code == 200

    assert events.seen[0] == "segredo user.hash"
    assert "tracing abre" in events.seen
    assert events.seen[-3:] == ["serve", "engine dispose", "tracing fecha"]
