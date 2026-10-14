"""Transferência de bytes na borda HTTP: prazos, envio por streaming e resposta de download.

Ver docs/GoblinShiftPlan.md, 1.7 (até 10 minutos por transferência e 30 s sem progresso) e 3.6
(cabeçalhos de download). Nenhuma peça daqui abre transação: quem chama faz as transações curtas
antes e depois da transferência (3.2).
"""

import logging
import unicodedata
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import BinaryIO
from urllib.parse import quote

import anyio
import anyio.from_thread
import anyio.to_thread
from fastapi import Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.files.limits import TRANSFER_IDLE_TIMEOUT, TRANSFER_MAX_DURATION

logger = logging.getLogger(__name__)

OCTET_STREAM = "application/octet-stream"
DOWNLOAD_CHUNK_BYTES = 64 * 1024
# Corpo binário publicado no OpenAPI: a rota lê o stream, então o FastAPI não o infere.
OCTET_STREAM_BODY: dict[str, object] = {
    "requestBody": {
        "required": True,
        "content": {OCTET_STREAM: {"schema": {"type": "string", "format": "binary"}}},
    }
}
# Cabeçalhos de 3.6: o conteúdo do usuário é servido na mesma origem que emite chaves, então
# nenhum arquivo pode virar página ativa nem ficar em cache intermediário.
_DOWNLOAD_PROTECTION_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}
# Intervalo sugerido quando não há vaga: transferências duram até 10 min, então tentar de novo a
# cada poucos segundos basta sem martelar a API.
TRANSFER_RETRY_AFTER_SECONDS = 10
_ASCII_FALLBACK_CHAR = "_"
_ASCII_PRINTABLE = range(0x20, 0x7F)


@dataclass(frozen=True)
class TransferTimeouts:
    """Prazos de uma transferência; a aplicação usa os de 1.7 e os testes os encurtam."""

    total: timedelta = TRANSFER_MAX_DURATION
    idle: timedelta = TRANSFER_IDLE_TIMEOUT


class TransferTimeoutError(Exception):
    """A transferência passou do prazo total ou ficou parada além do prazo sem progresso."""


def transfer_limit_problem() -> ApiProblem:
    """429 para envio ou download sem vaga de transferência simultânea (1.7)."""
    return ApiProblem(
        ProblemCode.RATE_LIMITED,
        "Limite de transferências simultâneas atingido; tente de novo depois do Retry-After.",
        headers={"Retry-After": str(TRANSFER_RETRY_AFTER_SECONDS)},
    )


def timeouts_of(request: Request) -> TransferTimeouts:
    timeouts: TransferTimeouts = request.app.state.transfer_timeouts
    return timeouts


def is_octet_stream(request: Request) -> bool:
    """`True` se o corpo foi declarado como `application/octet-stream` (parâmetros ignorados)."""
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    return media_type == OCTET_STREAM


def request_body_chunks(request: Request, timeouts: TransferTimeouts) -> Iterator[bytes]:
    """Pedaços do corpo da requisição, para consumir numa worker thread do anyio.

    O serviço de upload é síncrono e recebe um iterável; cada pedaço é buscado no event loop
    com `from_thread.run`, sem acumular o corpo em memória. Estoura `TransferTimeoutError` se
    um pedaço demorar mais que o prazo sem progresso ou se o prazo total acabar.
    """
    stream = request.stream()
    deadline = anyio.from_thread.run_sync(anyio.current_time) + timeouts.total.total_seconds()

    async def next_chunk() -> bytes | None:
        remaining = deadline - anyio.current_time()
        try:
            with anyio.fail_after(min(timeouts.idle.total_seconds(), remaining)):
                return await anext(stream, None)
        except TimeoutError:
            raise TransferTimeoutError("o envio passou do prazo") from None

    while (chunk := anyio.from_thread.run(next_chunk)) is not None:
        if chunk:
            yield chunk


def content_disposition(filename: str) -> str:
    """`attachment` com o nome em `filename*` (RFC 6266/8187) e um fallback ASCII em `filename`."""
    return (
        f'attachment; filename="{_ascii_fallback(filename)}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


class DownloadResponse(StreamingResponse):
    """Download por streaming com os cabeçalhos de 3.6, prazos e encerramento garantido.

    `on_close` roda sempre, uma vez, ao fim da resposta: download completo, cliente que
    desconecta, cliente que para de ler ou erro. Roda numa worker thread e protegido de
    cancelamento, porque encerra o uso do objeto e devolve a vaga no banco.
    """

    media_type = OCTET_STREAM

    def __init__(
        self,
        file: BinaryIO,
        *,
        size: int,
        filename: str,
        timeouts: TransferTimeouts,
        on_close: Callable[[], None],
    ) -> None:
        super().__init__(
            _file_chunks(file),
            headers={
                "Content-Disposition": content_disposition(filename),
                # O cliente percebe um download cortado pelo tamanho que faltou.
                "Content-Length": str(size),
                **_DOWNLOAD_PROTECTION_HEADERS,
            },
        )
        self._file = file
        self._timeouts = timeouts
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self._close)

    async def stream_response(self, send: Send) -> None:
        idle = self._timeouts.idle.total_seconds()
        with anyio.move_on_after(self._timeouts.total.total_seconds()) as total_scope:
            await send({"type": "http.response.start", "status": 200, "headers": self.raw_headers})
            async for chunk in self.body_iterator:
                try:
                    with anyio.fail_after(idle):
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                except TimeoutError:
                    raise TransferTimeoutError("o cliente parou de ler o download") from None
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        if total_scope.cancelled_caught:
            raise TransferTimeoutError("o download passou do prazo total")

    def _close(self) -> None:
        try:
            self._file.close()
        finally:
            self._on_close()


async def _file_chunks(file: BinaryIO) -> AsyncIterator[bytes]:
    while chunk := await anyio.to_thread.run_sync(file.read, DOWNLOAD_CHUNK_BYTES):
        yield chunk


def _ascii_fallback(filename: str) -> str:
    """Para clientes sem RFC 8187: sem acentos, e `"`, `\\` e não ASCII viram `_`."""
    decomposed = unicodedata.normalize("NFKD", filename)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(
        c if ord(c) in _ASCII_PRINTABLE and c not in '"\\' else _ASCII_FALLBACK_CHAR
        for c in without_marks
    )
