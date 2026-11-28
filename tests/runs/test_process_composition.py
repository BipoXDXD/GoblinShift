"""Composição dos processos: a ordem dos efeitos na subida e na parada (auditoria, achado 7).

Caracterização: fixa o comportamento de hoje da API, do `gs-worker`, do `gs-maintenance` e do
emissor do `run.finished` do `gs-admin`, para a composição poder mudar de módulo sem mudar a ordem.
Os pontos de troca são os que não mudam com o módulo: o `OTLPSpanExporter` da telemetria (abrir e
fechar o tracing), os segredos lidos em `Settings` e os nomes que cada processo usa no próprio
módulo. Sem banco: o engine aponta para a porta 1 e nunca conecta.

O escopo dos spans do processo fica em `test_worker_tracer.py`; aqui, só a ordem.
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

from goblinshift.entrypoints import admin_cli, bootstrap, maintenance, worker
from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.maintenance import Cycle
from goblinshift.entrypoints.shutdown import ShutdownFlag
from goblinshift.platform import telemetry
from goblinshift.platform.db import PoolSize, make_engine
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.finished import RunFinishedReporter
from tests.span_exporters import BATCH_DELAY_MS


class Events:
    """Linha do tempo dos efeitos da subida e da parada, na ordem em que aconteceram."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.seen: list[str] = []
        self._monkeypatch = monkeypatch

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
        return _Recording()

    def engine_maker(self) -> Callable[..., Engine]:
        """`make_engine` de verdade (não conecta), com a criação e o `dispose` na linha do tempo."""

        def make(settings: Settings, *, pool: PoolSize | None = None) -> Engine:
            self.add("engine cria")
            engine = make_engine(settings, pool=pool)
            dispose = engine.dispose

            def recorded_dispose(close: bool = True) -> None:
                self.add("engine dispose")
                dispose(close)

            self._monkeypatch.setattr(engine, "dispose", recorded_dispose)
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
    timeline = Events(monkeypatch)
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

    class Listener:
        def __init__(self, _engine: object, _connect_timeout_s: int) -> None:
            events.add("listen abre")

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            events.add("listen fecha")

        def wait(self, _timeout_s: float) -> bool:
            return False

    def one_turn(*_args: object) -> bool:
        events.add("volta")
        shutdown.request()
        return True

    monkeypatch.setattr(worker, "make_engine", events.engine_maker())
    monkeypatch.setattr(worker, "QueueListener", Listener)
    monkeypatch.setattr(worker, "RunFinishedReporter", events.reporter_maker())
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


# gs-admin ---------------------------------------------------------------------------------------


def test_emissor_do_admin_le_o_segredo_antes_de_abrir_o_tracing(
    events: Events, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pelo `_run_finished_reporter` do `gs-admin`, que é privado.

    Os comandos públicos que abrem o emissor (`block`, `request-deletion`) escrevem no banco antes
    e depois dele; sem Postgres, este é o ponto mais perto do comando que ainda abre o emissor.
    """
    settings = _settings(tmp_path)
    factory: sessionmaker[Session] = sessionmaker()
    context = admin_cli._Context(factory=factory, settings=settings, clock=lambda: 0.0)
    monkeypatch.setattr(bootstrap, "RunFinishedReporter", events.reporter_maker())

    with admin_cli._run_finished_reporter(context):
        events.add("comando")

    assert events.seen == [
        "segredo user.hash",
        "tracing abre",
        "reporter",
        "comando",
        "tracing fecha",
    ]


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

    assert events.seen == [
        "segredo user.hash",
        "segredo do banco",
        "tracing abre",
        "serve",
        "engine dispose",
        "tracing fecha",
    ]
