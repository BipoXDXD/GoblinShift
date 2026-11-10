"""Parada do worker e da manutenção: o tracing exporta o lote pendente por último e com prazo.

Sem banco: o provider usa o `BatchSpanProcessor` de produção, com o exportador OTLP trocado por um
falso e um atraso de lote longo o bastante para o span ficar na fila até a parada. Os ciclos, o
laço e o `LISTEN` são trocados por falsos, e o engine nunca conecta.
"""

import json
import logging
import signal
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Self
from unittest.mock import Mock

import pytest
import yaml
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints import maintenance, worker
from goblinshift.entrypoints.maintenance import CYCLE_SPAN, AccountJobs, Cycle, run_cycles
from goblinshift.entrypoints.shutdown import SHUTDOWN_POLL_S, ShutdownFlag
from goblinshift.platform import telemetry
from goblinshift.platform.db import make_engine
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import UserHasher
from goblinshift.runs.finished import RunFinishedReporter
from goblinshift.runs.maintenance import Maintenance
from tests.span_exporters import (
    BATCH_DELAY_MS,
    SHORT_SHUTDOWN_TIMEOUT_S,
    BrokenShutdownExporter,
    StuckExporter,
)
from tests.test_api_server_limits import (
    COMPOSE_BASE,
    COMPOSE_DEFAULT_STOP_GRACE_SECONDS,
    COMPOSE_PROD,
    COOLIFY_APP,
)

PENDING_SPAN = "span na fila"
SHUTDOWN_SPAN = "passo anterior da parada"
DEADLINE_WARNING = "encerramento do tracing passou do prazo"
FAILURE_ERROR = "encerramento do tracing falhou"


class Exporters:
    """Fábrica do `OTLPSpanExporter`: um exportador por provider, como em produção."""

    def __init__(self, make: Callable[[], SpanExporter] = InMemorySpanExporter) -> None:
        self.created: list[SpanExporter] = []
        self._make = make

    def __call__(self) -> SpanExporter:
        exporter = self._make()
        self.created.append(exporter)
        return exporter

    def span_names(self) -> list[str]:
        return [
            span.name
            for exporter in self.created
            if isinstance(exporter, InMemorySpanExporter)
            for span in exporter.get_finished_spans()
        ]

    def release(self) -> None:
        for exporter in self.created:
            if isinstance(exporter, StuckExporter):
                exporter.gate.set()


@pytest.fixture
def otlp(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://coletor.invalid")
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", BATCH_DELAY_MS)
    return monkeypatch


@pytest.fixture
def exporters(otlp: pytest.MonkeyPatch) -> Exporters:
    exporters = Exporters()
    otlp.setattr(telemetry, "OTLPSpanExporter", exporters)
    return exporters


@pytest.fixture
def stuck_exporters(otlp: pytest.MonkeyPatch) -> Iterator[Exporters]:
    otlp.setattr(telemetry, "TRACING_SHUTDOWN_TIMEOUT_S", SHORT_SHUTDOWN_TIMEOUT_S)
    exporters = Exporters(StuckExporter)
    otlp.setattr(telemetry, "OTLPSpanExporter", exporters)
    try:
        yield exporters
    finally:
        exporters.release()


def _settings(tmp_path: Path) -> Settings:
    return Settings(environment="test", db_port=1, storage_root=tmp_path / "storage")


def _messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno == level]


# Worker ----------------------------------------------------------------------------------------


def test_parada_do_worker_exporta_o_span_que_ficou_na_fila(exporters: Exporters) -> None:
    with worker.process_tracer(Settings(environment="test"), "goblinshift-worker") as tracer:
        tracer.start_span(PENDING_SPAN).end()
        assert exporters.span_names() == []

    assert exporters.span_names() == [PENDING_SPAN]


def test_prazo_estourado_no_encerramento_do_tracing_do_worker_fica_no_log(
    stuck_exporters: Exporters, caplog: pytest.LogCaptureFixture
) -> None:
    with worker.process_tracer(Settings(environment="test"), "goblinshift-worker") as tracer:
        tracer.start_span(PENDING_SPAN).end()

    assert any(DEADLINE_WARNING in message for message in _messages(caplog, logging.WARNING))


def test_falha_ao_encerrar_o_tracing_do_worker_nao_derruba_a_parada(
    otlp: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    exporters = Exporters(BrokenShutdownExporter)
    otlp.setattr(telemetry, "OTLPSpanExporter", exporters)

    with worker.process_tracer(Settings(environment="test"), "goblinshift-worker") as tracer:
        tracer.start_span(PENDING_SPAN).end()

    assert exporters.span_names() == [PENDING_SPAN]
    assert any(FAILURE_ERROR in message for message in _messages(caplog, logging.ERROR))


class _IdleListener:
    """`QueueListener` sem banco, que nunca recebe NOTIFY."""

    def __init__(self, _engine: object, _connect_timeout_s: int) -> None:
        return None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def wait(self, _timeout_s: float) -> bool:
        return False


def _dispose_emitting_span(
    otlp: pytest.MonkeyPatch, make_engine: Callable[[Settings], Engine], tracers: list[Tracer]
) -> Callable[[Settings], Engine]:
    """`make_engine` cujo `dispose` emite um span pelo tracer do processo antes de fechar o pool.

    Representa qualquer passo da parada que ainda emite span antes do encerramento do tracing.
    """

    def make(settings: Settings) -> Engine:
        engine = make_engine(settings)
        dispose = engine.dispose

        def dispose_emitting_span() -> None:
            with tracers[0].start_as_current_span(SHUTDOWN_SPAN):
                dispose()

        otlp.setattr(engine, "dispose", dispose_emitting_span)
        return engine

    return make


def test_parada_do_worker_encerra_o_tracing_depois_dos_outros_passos(
    exporters: Exporters, otlp: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shutdown = ShutdownFlag()
    tracers: list[Tracer] = []

    def capture_tracer(
        factory: sessionmaker[Session], hasher: UserHasher, tracer: Tracer
    ) -> RunFinishedReporter:
        tracers.append(tracer)
        return RunFinishedReporter(factory, hasher, tracer)

    def last_attempt(*_args: object) -> bool:
        shutdown.request(signal.SIGTERM)
        return True

    otlp.setattr(worker, "RunFinishedReporter", capture_tracer)
    otlp.setattr(worker, "QueueListener", _IdleListener)
    otlp.setattr(worker, "_work_once", last_attempt)
    otlp.setattr(worker, "make_engine", _dispose_emitting_span(otlp, make_engine, tracers))

    worker.serve(_settings(tmp_path), Mock(), shutdown)

    assert SHUTDOWN_SPAN in exporters.span_names()


# Manutenção ------------------------------------------------------------------------------------


def _one_round(otlp: pytest.MonkeyPatch, shutdown: ShutdownFlag) -> None:
    """Ciclos falsos: uma volta só, e a parada chega no fim dela."""

    def executions(_self: Maintenance, should_stop: Callable[[], bool]) -> Mapping[str, int]:
        return {}

    def accounts(_self: AccountJobs) -> Mapping[str, int]:
        shutdown.request(signal.SIGTERM)
        return {}

    otlp.setattr(Maintenance, "run_cycle", executions)
    otlp.setattr(AccountJobs, "run_cycle", accounts)


def test_parada_da_manutencao_exporta_o_ciclo_que_ficou_na_fila(
    exporters: Exporters, otlp: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shutdown = ShutdownFlag()
    _one_round(otlp, shutdown)

    maintenance.serve(_settings(tmp_path), Mock(), shutdown)

    assert CYCLE_SPAN in exporters.span_names()


def test_prazo_estourado_no_encerramento_do_tracing_da_manutencao_fica_no_log(
    stuck_exporters: Exporters,
    otlp: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    shutdown = ShutdownFlag()
    _one_round(otlp, shutdown)

    maintenance.serve(_settings(tmp_path), Mock(), shutdown)

    assert any(DEADLINE_WARNING in message for message in _messages(caplog, logging.WARNING))


def test_manutencao_espera_o_prazo_do_tracing_uma_vez_so(
    stuck_exporters: Exporters, otlp: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Um provider por processo: a parada soma um prazo de encerramento, não um por provider.
    shutdown = ShutdownFlag()
    _one_round(otlp, shutdown)

    maintenance.serve(_settings(tmp_path), Mock(), shutdown)

    assert len(stuck_exporters.created) == 1


def test_parada_da_manutencao_encerra_o_tracing_depois_dos_outros_passos(
    exporters: Exporters, otlp: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shutdown = ShutdownFlag()
    _one_round(otlp, shutdown)
    tracers: list[Tracer] = []

    def capture_tracer(
        tracer: Tracer, cycles: Sequence[Cycle], should_stop: Callable[[], bool]
    ) -> None:
        tracers.append(tracer)
        run_cycles(tracer, cycles, should_stop)

    otlp.setattr(maintenance, "run_cycles", capture_tracer)
    otlp.setattr(maintenance, "make_engine", _dispose_emitting_span(otlp, make_engine, tracers))

    maintenance.serve(_settings(tmp_path), Mock(), shutdown)

    assert SHUTDOWN_SPAN in exporters.span_names()


class _DisposeFailedError(RuntimeError):
    pass


def test_falha_ao_descartar_o_pool_da_manutencao_nao_pula_o_encerramento_do_tracing(
    exporters: Exporters, otlp: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Sem o encerramento com prazo, sobraria o `atexit` do SDK, que espera o Collector sem prazo.
    shutdown = ShutdownFlag()
    _one_round(otlp, shutdown)

    def make_engine_failing_dispose(settings: Settings) -> Engine:
        engine = make_engine(settings)
        dispose = engine.dispose

        def dispose_and_fail() -> None:
            dispose()
            raise _DisposeFailedError

        otlp.setattr(engine, "dispose", dispose_and_fail)
        return engine

    otlp.setattr(maintenance, "make_engine", make_engine_failing_dispose)

    with pytest.raises(_DisposeFailedError):
        maintenance.serve(_settings(tmp_path), Mock(), shutdown)

    assert exporters.span_names() == [CYCLE_SPAN]


# Carência da plataforma ------------------------------------------------------------------------


def _service(name: str, *files: Path) -> dict[str, Any]:
    service: dict[str, Any] = {}
    for file in files:
        service.update(yaml.safe_load(file.read_text(encoding="utf-8"))["services"].get(name, {}))
    return service


def _coolify_service(name: str) -> dict[str, Any]:
    service: dict[str, Any] = json.loads(COOLIFY_APP.read_text(encoding="utf-8"))["services"][name]
    return service


def _grace_s(service: dict[str, Any]) -> float:
    grace = str(service.get("stop_grace_period", f"{COMPOSE_DEFAULT_STOP_GRACE_SECONDS}s"))
    assert grace.endswith("s") and grace[:-1].isdigit(), f"duração inesperada: {grace}"
    return float(grace[:-1])


def _deployments(name: str) -> list[Any]:
    return [
        pytest.param(_service(name, COMPOSE_BASE), id=f"{name}-compose-local"),
        pytest.param(_service(name, COMPOSE_BASE, COMPOSE_PROD), id=f"{name}-compose-prod"),
        pytest.param(_coolify_service(name), id=f"{name}-coolify-app-json"),
    ]


def _idle_or_database_down_step_s() -> float:
    """Teto do passo em curso quando o SIGTERM chega, com o processo ocioso ou o banco fora.

    Ocioso, o processo dorme em fatias de `SHUTDOWN_POLL_S`; com o banco fora, a conexão desiste
    em `db_connect_timeout_s`. O banco lento fica de fora: um statement pode levar o
    `statement_timeout`, que passa da carência (comentário dos ciclos em `maintenance.serve`).
    """
    connect_timeout_s: float = Settings.model_fields["db_connect_timeout_s"].default
    return max(SHUTDOWN_POLL_S, connect_timeout_s)


@pytest.mark.parametrize("service", _deployments("maintenance"))
def test_parada_da_manutencao_ociosa_ou_sem_banco_com_o_prazo_do_tracing_cabe_na_carencia(
    service: dict[str, Any],
) -> None:
    # A volta em curso para entre os passos. Com o banco lento, o SIGKILL pode chegar antes: risco
    # aceito, que perde spans, não dados.
    step_s = _idle_or_database_down_step_s()

    assert _grace_s(service) > step_s + telemetry.TRACING_SHUTDOWN_TIMEOUT_S


@pytest.mark.parametrize("service", _deployments("worker"))
def test_parada_do_worker_ocioso_ou_sem_banco_com_o_prazo_do_tracing_cabe_na_carencia(
    service: dict[str, Any],
) -> None:
    # A tentativa em curso pode passar da carência de propósito (docstring do worker); o prazo do
    # tracing não pode tirar do worker ocioso a parada graciosa. Ocioso, ele ainda busca na fila,
    # e com o banco fora o `claim_next` e o `LISTEN` esperam a conexão desistir.
    step_s = _idle_or_database_down_step_s()

    assert _grace_s(service) > step_s + telemetry.TRACING_SHUTDOWN_TIMEOUT_S
