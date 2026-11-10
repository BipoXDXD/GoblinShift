"""Sinal de saturação da API: recusas do teto de concorrência do uvicorn, por janela.

Acima de `--limit-concurrency` (docker/app.Dockerfile), o uvicorn responde 503 antes do ASGI: o
pedido não abre span, não conta em SLO nenhum e só deixa a linha `Exceeded concurrency limit.` no
logger `uvicorn.error`, que não é exportado. O contador conta essas linhas no próprio processo, e a
cada janela a contagem sai num span `api.saturation` com `main = true`, pelo mesmo exportador dos
traces da API, que o alerta `causa-api-saturada` lê (deploy/openobserve/README.md). A mensagem não
traz caminho nem cliente, e só a contagem sai.
"""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from opentelemetry.trace import Tracer

from goblinshift.platform.telemetry import UVICORN_ERROR_LOGGER

# Texto fixo do uvicorn (h11_impl e httptools_impl); não é API pública, e
# tests/openobserve/test_saturation_runbook.py confere que ainda é o que ele escreve.
UVICORN_CONCURRENCY_MESSAGE = "Exceeded concurrency limit."
SATURATION_SPAN = "api.saturation"
REJECTED_ATTRIBUTE = "api.saturation.rejected"
# Uma janela por minuto: o alerta conta os minutos com recusa, não o volume delas.
WINDOW_S = 60

logger = logging.getLogger(__name__)


class ConcurrencyLimitCounter(logging.Handler):
    """Conta as recusas do teto de concorrência que o uvicorn registra.

    A soma e a leitura com zeragem dividem um lock próprio: o contador vale entre threads. O nível
    WARNING deixa o `Logger` descartar o resto antes do handler, e cada recusa custa uma
    comparação de texto e uma soma.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._count_lock = threading.Lock()
        self._rejected = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.getMessage() != UVICORN_CONCURRENCY_MESSAGE:
                return
            with self._count_lock:
                self._rejected += 1
        except Exception:
            self.handleError(record)

    def take(self) -> int:
        """Recusas desde a chamada anterior; o contador recomeça do zero."""
        with self._count_lock:
            rejected, self._rejected = self._rejected, 0
        return rejected


def report_window(tracer: Tracer, counter: ConcurrencyLimitCounter) -> None:
    """Exporta as recusas da janela num span raiz; janela sem recusa não exporta nada."""
    rejected = counter.take()
    if rejected == 0:
        return
    with tracer.start_as_current_span(SATURATION_SPAN) as span:
        span.set_attribute("main", True)
        span.set_attribute(REJECTED_ATTRIBUTE, rejected)


async def wait_one_window() -> None:
    await asyncio.sleep(WINDOW_S)


@asynccontextmanager
async def saturation_reporting(
    tracer: Tracer, *, wait: Callable[[], Awaitable[None]] = wait_one_window
) -> AsyncIterator[None]:
    """Conta as recusas enquanto a API roda e relata uma janela a cada `wait`.

    Fica no lifespan: o laço precisa do event loop do uvicorn, o handler sai do logger quando a
    API para (a fábrica roda várias vezes nos testes, no mesmo processo), e a parada relata a
    janela incompleta em vez de perdê-la.
    """
    counter = ConcurrencyLimitCounter()
    uvicorn_error = logging.getLogger(UVICORN_ERROR_LOGGER)
    uvicorn_error.addHandler(counter)
    task = asyncio.create_task(_report_every_window(tracer, counter, wait))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        uvicorn_error.removeHandler(counter)
        _report_safely(tracer, counter)


async def _report_every_window(
    tracer: Tracer, counter: ConcurrencyLimitCounter, wait: Callable[[], Awaitable[None]]
) -> None:
    while True:
        await wait()
        _report_safely(tracer, counter)


def _report_safely(tracer: Tracer, counter: ConcurrencyLimitCounter) -> None:
    # Fronteira do laço: uma janela que não sai custa só a contagem dela, nunca o relato seguinte
    # nem a parada da API.
    try:
        report_window(tracer, counter)
    except Exception:
        logger.exception("relato de saturação falhou")
