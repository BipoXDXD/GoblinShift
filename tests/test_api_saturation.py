"""Sinal de saturação da API: as recusas do teto de concorrência do uvicorn viram um span.

Acima de `--limit-concurrency`, o uvicorn responde 503 antes do ASGI, sem span, e só escreve
`Exceeded concurrency limit.` no logger `uvicorn.error`. O contador conta essas linhas e o relato
exporta a contagem de cada janela num span `api.saturation`, que o alerta
`causa-api-saturada` lê no stream de traces.
"""

import asyncio
import logging
import socket
import threading
from collections.abc import Iterator
from typing import Any

import httpx2 as httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.saturation import (
    REJECTED_ATTRIBUTE,
    SATURATION_SPAN,
    UVICORN_CONCURRENCY_MESSAGE,
    ConcurrencyLimitCounter,
    report_window,
    saturation_reporting,
    seconds_until_window_end,
)
from goblinshift.platform import telemetry
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import UVICORN_ERROR_LOGGER

UVICORN_ERROR = logging.getLogger(UVICORN_ERROR_LOGGER)
# Bem acima da duração do teste: o lote do `BatchSpanProcessor` só sai pelo encerramento do
# tracing, nunca pelo relógio do SDK.
BATCH_DELAY_MS = "600000"


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer(exporter: InMemorySpanExporter) -> Tracer:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer(__name__)


def _record(message: str, level: int = logging.WARNING, *args: object) -> logging.LogRecord:
    return logging.LogRecord(UVICORN_ERROR_LOGGER, level, __file__, 1, message, args, None)


def _saturation_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [span for span in exporter.get_finished_spans() if span.name == SATURATION_SPAN]


def _rejections(exporter: InMemorySpanExporter) -> list[object]:
    return [(span.attributes or {})[REJECTED_ATTRIBUTE] for span in _saturation_spans(exporter)]


# --- Contador -----------------------------------------------------------------------------------


def test_contador_sem_recusa_devolve_zero() -> None:
    assert ConcurrencyLimitCounter().take() == 0


def test_contador_conta_cada_recusa_do_teto() -> None:
    counter = ConcurrencyLimitCounter()

    for _ in range(3):
        counter.handle(_record(UVICORN_CONCURRENCY_MESSAGE))

    assert counter.take() == 3


def test_contador_zera_a_cada_janela() -> None:
    counter = ConcurrencyLimitCounter()
    counter.handle(_record(UVICORN_CONCURRENCY_MESSAGE))
    counter.take()

    assert counter.take() == 0


@pytest.mark.parametrize(
    "message",
    [
        "Exceeded concurrency limit",  # sem o ponto: outra mensagem
        "Invalid HTTP request received.",
        "Started server process [%d]",
        "",
    ],
)
def test_contador_ignora_outras_mensagens_do_uvicorn(message: str) -> None:
    counter = ConcurrencyLimitCounter()

    counter.handle(_record(message))

    assert counter.take() == 0


def test_contador_le_a_mensagem_formatada_com_argumentos() -> None:
    counter = ConcurrencyLimitCounter()

    counter.handle(_record("Exceeded %s limit.", logging.WARNING, "concurrency"))

    assert counter.take() == 1


def test_contador_nao_recebe_registros_abaixo_de_warning_pelo_logger() -> None:
    counter = ConcurrencyLimitCounter()
    logger = logging.getLogger("tests.saturation.nivel")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(counter)
    try:
        logger.info(UVICORN_CONCURRENCY_MESSAGE)
    finally:
        logger.removeHandler(counter)

    assert counter.take() == 0


# --- Relato de uma janela -----------------------------------------------------------------------


def test_janela_com_recusas_vira_um_span_main_com_a_contagem(
    tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    counter = ConcurrencyLimitCounter()
    for _ in range(4):
        counter.handle(_record(UVICORN_CONCURRENCY_MESSAGE))

    report_window(tracer, counter)

    (span,) = _saturation_spans(exporter)
    assert dict(span.attributes or {}) == {"main": True, REJECTED_ATTRIBUTE: 4}
    assert span.parent is None
    assert counter.take() == 0


def test_janela_sem_recusa_nao_exporta_span(tracer: Tracer, exporter: InMemorySpanExporter) -> None:
    report_window(tracer, ConcurrencyLimitCounter())

    assert exporter.get_finished_spans() == ()


# --- Fim da janela -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "delay"),
    [
        (120.0, 61.0),  # na virada: espera a próxima inteira
        (120.9, 60.1),  # logo depois do relato de uma virada
        (150.0, 31.0),  # meio do minuto: só o resto dele
        (179.5, 1.5),  # perto da virada: não relata duas vezes no mesmo minuto
        (1_762_750_799.0, 2.0),  # época real: 1 s antes da virada
    ],
)
def test_janela_termina_logo_depois_da_virada_do_minuto_de_relogio(
    now: float, delay: float
) -> None:
    # Todas as réplicas relatam no mesmo segundo depois da virada, e o span cai no minuto
    # absoluto seguinte ao que ele conta: o alerta agrupa pelo minuto do `_timestamp`.
    assert seconds_until_window_end(now) == pytest.approx(delay)


# --- Relato periódico ---------------------------------------------------------------------------


class Ticks:
    """Fim de janela sob controle do teste, sem relógio real."""

    def __init__(self) -> None:
        self._ticks: asyncio.Queue[None] = asyncio.Queue()
        self.waiting = asyncio.Event()

    async def wait(self) -> None:
        self.waiting.set()
        await self._ticks.get()
        self.waiting.clear()

    async def tick(self) -> None:
        self._ticks.put_nowait(None)
        await asyncio.sleep(0)
        await self.waiting.wait()


def test_relata_cada_janela_e_a_ultima_ao_sair(
    tracer: Tracer, exporter: InMemorySpanExporter
) -> None:
    async def scenario() -> None:
        ticks = Ticks()
        async with saturation_reporting(tracer, wait=ticks.wait):
            await ticks.waiting.wait()
            UVICORN_ERROR.warning(UVICORN_CONCURRENCY_MESSAGE)
            UVICORN_ERROR.warning(UVICORN_CONCURRENCY_MESSAGE)
            await ticks.tick()
            await ticks.tick()  # janela sem recusa: nenhum span
            UVICORN_ERROR.warning(UVICORN_CONCURRENCY_MESSAGE)
            assert _rejections(exporter) == [2]
        # A janela incompleta sai na parada, em vez de se perder.
        assert _rejections(exporter) == [2, 1]

    asyncio.run(scenario())


def test_handler_sai_do_logger_do_uvicorn_ao_terminar(tracer: Tracer) -> None:
    async def scenario() -> None:
        before = list(UVICORN_ERROR.handlers)
        async with saturation_reporting(tracer, wait=Ticks().wait):
            assert len(UVICORN_ERROR.handlers) == len(before) + 1
        assert UVICORN_ERROR.handlers == before

    asyncio.run(scenario())


def test_falha_ao_exportar_uma_janela_nao_para_o_relato(
    exporter: InMemorySpanExporter, caplog: pytest.LogCaptureFixture
) -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real = provider.get_tracer(__name__)

    class FailsOnce:
        def __init__(self) -> None:
            self.failed = False

        def start_as_current_span(self, *args: object, **kwargs: object) -> object:
            if not self.failed:
                self.failed = True
                raise RuntimeError("exportador fora")
            return real.start_as_current_span(*args, **kwargs)  # type: ignore[arg-type]

    async def scenario() -> None:
        ticks = Ticks()
        async with saturation_reporting(FailsOnce(), wait=ticks.wait):  # type: ignore[arg-type]
            await ticks.waiting.wait()
            UVICORN_ERROR.warning(UVICORN_CONCURRENCY_MESSAGE)
            await ticks.tick()
            UVICORN_ERROR.warning(UVICORN_CONCURRENCY_MESSAGE)
            await ticks.tick()
            assert _rejections(exporter) == [1]

    asyncio.run(scenario())
    assert "relato de saturação falhou" in caplog.text


# --- Ligação na API -----------------------------------------------------------------------------


def test_api_exporta_as_recusas_do_teto_pelo_mesmo_exportador_dos_traces(
    exporter: InMemorySpanExporter,
) -> None:
    app = create_app(Settings(environment="test", db_port=1), span_exporter=exporter)
    try:
        with TestClient(app):
            UVICORN_ERROR.warning(UVICORN_CONCURRENCY_MESSAGE)
    finally:
        # O instrumentor do SQLAlchemy é global: sem isso, os engines dos próximos testes
        # continuariam mandando spans para este exportador.
        SQLAlchemyInstrumentor().uninstrument()

    assert _rejections(exporter) == [1]


def test_parada_da_api_exporta_a_janela_final_antes_de_encerrar_o_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    # O `BatchSpanProcessor` de produção: o que chegar depois do encerramento do tracing é
    # descartado, então a janela final só sai se o relato da parada vier antes dele.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://coletor.invalid")
    monkeypatch.setenv("OTEL_BSP_SCHEDULE_DELAY", BATCH_DELAY_MS)
    monkeypatch.setattr(telemetry, "OTLPSpanExporter", lambda: exporter)
    app = create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))
    try:
        with TestClient(app):
            UVICORN_ERROR.warning(UVICORN_CONCURRENCY_MESSAGE)
            assert _saturation_spans(exporter) == []
    finally:
        SQLAlchemyInstrumentor().uninstrument()

    assert _rejections(exporter) == [1]


# --- Uvicorn de verdade -------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class Gate:
    """O pedido que ocupa a vaga avisa que entrou e espera o teste liberá-lo."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()


@pytest.fixture
def saturated_server() -> Iterator[tuple[str, Gate, int]]:
    """Uvicorn com teto 2: a conexão de um pedido em curso já ocupa o que ele atende."""
    gate = Gate()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        gate.entered.set()
        await asyncio.to_thread(gate.release.wait, 10)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, limit_concurrency=2, lifespan="off", log_config=None
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(500):
            if server.started:
                break
            gate.release.wait(0.01)
        assert server.started, "uvicorn não subiu"
        assert thread.ident is not None
        yield f"http://127.0.0.1:{port}", gate, thread.ident
    finally:
        gate.release.set()
        server.should_exit = True
        thread.join(10)


class ThreadsOfRejections(logging.Handler):
    """Em que thread o uvicorn registrou cada recusa."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.seen: list[int | None] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == UVICORN_CONCURRENCY_MESSAGE:
            self.seen.append(record.thread)


def test_recusa_real_do_uvicorn_e_contada(saturated_server: tuple[str, Gate, int]) -> None:
    url, gate, server_thread = saturated_server
    counter = ConcurrencyLimitCounter()
    threads = ThreadsOfRejections()
    UVICORN_ERROR.addHandler(counter)
    UVICORN_ERROR.addHandler(threads)
    try:
        with httpx.Client(timeout=5) as busy, httpx.Client(timeout=5) as other:
            pending = threading.Thread(target=busy.get, args=(url,))
            pending.start()
            assert gate.entered.wait(5), "o primeiro pedido não chegou à aplicação"
            refused = other.get(url)
            gate.release.set()
            pending.join(5)
    finally:
        UVICORN_ERROR.removeHandler(counter)
        UVICORN_ERROR.removeHandler(threads)

    assert refused.status_code == 503
    assert counter.take() == 1
    # A recusa vem da thread do event loop do uvicorn, a mesma do laço que relata as janelas.
    assert threads.seen == [server_thread]
