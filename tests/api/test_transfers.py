"""Peças HTTP das transferências, sem banco: nome do anexo, piso de vazão e fim do download."""

import io
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any, cast

import anyio
import pytest
from fastapi import Request
from starlette.requests import ClientDisconnect
from starlette.types import Message

from goblinshift.entrypoints.api.transfers import (
    DownloadResponse,
    TransferTimeoutError,
    TransferTimeouts,
    content_disposition,
    is_below_min_rate,
    request_body_chunks,
)

GENEROUS = TransferTimeouts(total=timedelta(seconds=10), idle=timedelta(seconds=10))
HTTP_SCOPE: dict[str, Any] = {"type": "http", "asgi": {"spec_version": "2.4"}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("saida.csv", "attachment; filename=\"saida.csv\"; filename*=UTF-8''saida.csv"),
        (
            "relatório final.csv",
            'attachment; filename="relatorio final.csv"; '
            "filename*=UTF-8''relat%C3%B3rio%20final.csv",
        ),
        (
            'a"b\\c;d.txt',
            "attachment; filename=\"a_b_c;d.txt\"; filename*=UTF-8''a%22b%5Cc%3Bd.txt",
        ),
        ("日本.txt", "attachment; filename=\"__.txt\"; filename*=UTF-8''%E6%97%A5%E6%9C%AC.txt"),
    ],
)
def test_nome_do_anexo_segue_a_rfc_6266(name: str, expected: str) -> None:
    assert content_disposition(name) == expected


FLOOR_1000_AFTER_2S = TransferTimeouts(
    min_rate_bytes_per_s=1000, min_rate_grace=timedelta(seconds=2)
)


@pytest.mark.parametrize(
    ("received", "elapsed_s", "below"),
    [
        (0, 2.0, False),  # na carência, nem o envio parado é cobrado
        (0, 2.001, True),
        (3000, 3.0, False),  # exatamente no piso
        (2999, 3.0, True),
    ],
)
def test_piso_de_vazao_vale_so_depois_da_carencia(
    received: int, elapsed_s: float, below: bool
) -> None:
    assert is_below_min_rate(received, elapsed_s, FLOOR_1000_AFTER_2S) is below


class StreamedRequest:
    """O que `request_body_chunks` lê da `Request`: o corpo, pedaço a pedaço, como o Starlette.

    O gerador só anda quando a rota pede o próximo pedaço: as pausas dele são a espera pelo
    cliente, e o `b""` final marca o fim do corpo.
    """

    def __init__(self, chunks: list[bytes], pause_s: float, pause_before_end_s: float) -> None:
        self._chunks = chunks
        self._pause_s = pause_s
        self._pause_before_end_s = pause_before_end_s

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            await anyio.sleep(self._pause_s)
            yield chunk
        await anyio.sleep(self._pause_before_end_s)
        yield b""


async def consume(
    request: StreamedRequest, timeouts: TransferTimeouts, work_per_chunk_s: float
) -> int:
    """Lê o corpo numa worker thread, como `receive_content`, gastando `work_per_chunk_s` em cada
    pedaço (gravação e hash do servidor)."""

    def read() -> int:
        received = 0
        for chunk in request_body_chunks(cast(Request, request), timeouts):
            time.sleep(work_per_chunk_s)
            received += len(chunk)
        return received

    return await anyio.to_thread.run_sync(read)


@pytest.mark.anyio
async def test_piso_de_vazao_nao_cobra_o_tempo_do_servidor_entre_os_pedacos() -> None:
    # O cliente manda 100 B a cada 10 ms (10 KB/s), cinco vezes o piso; o servidor gasta 100 ms
    # em cada pedaço, e pelo relógio a média ficaria em ~900 B/s, abaixo do piso.
    timeouts = TransferTimeouts(
        total=timedelta(seconds=10),
        idle=timedelta(seconds=10),
        min_rate_bytes_per_s=2000,
        min_rate_grace=timedelta(milliseconds=20),
    )
    request = StreamedRequest([b"x" * 100] * 10, pause_s=0.01, pause_before_end_s=0)

    assert await consume(request, timeouts, work_per_chunk_s=0.1) == 1000


@pytest.mark.anyio
async def test_fim_do_corpo_nao_e_cobrado_pelo_piso_de_vazao() -> None:
    # Os 100 B chegam de uma vez; o `b""` final vem 300 ms depois. Cobrado no fim, ficaria em
    # ~330 B/s, abaixo do piso, com o corpo inteiro já recebido.
    timeouts = TransferTimeouts(
        total=timedelta(seconds=10),
        idle=timedelta(seconds=10),
        min_rate_bytes_per_s=1000,
        min_rate_grace=timedelta(milliseconds=100),
    )
    request = StreamedRequest([b"x" * 100], pause_s=0, pause_before_end_s=0.3)

    assert await consume(request, timeouts, work_per_chunk_s=0) == 100


class Recorder:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def response_for(
    content: bytes, recorder: Recorder, timeouts: TransferTimeouts
) -> DownloadResponse:
    return DownloadResponse(
        io.BytesIO(content),
        size=len(content),
        filename="x",
        timeouts=timeouts,
        on_close=recorder.close,
    )


async def receive() -> Message:
    await anyio.sleep_forever()
    raise AssertionError("inalcançável")


@pytest.mark.anyio
async def test_download_completo_encerra_uma_vez() -> None:
    recorder = Recorder()
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await response_for(b"abc", recorder, GENEROUS)(HTTP_SCOPE, receive, send)

    assert b"".join(m.get("body", b"") for m in sent) == b"abc"
    assert recorder.closed == 1


@pytest.mark.anyio
async def test_cliente_que_desconecta_no_meio_ainda_encerra_o_download() -> None:
    recorder = Recorder()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            raise OSError("conexão encerrada pelo cliente")

    with pytest.raises(ClientDisconnect):
        await response_for(b"abc", recorder, GENEROUS)(HTTP_SCOPE, receive, send)

    assert recorder.closed == 1


@pytest.mark.anyio
async def test_cliente_que_para_de_ler_e_cortado_e_o_download_encerrado() -> None:
    recorder = Recorder()
    timeouts = TransferTimeouts(total=timedelta(seconds=10), idle=timedelta(milliseconds=100))

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            await anyio.sleep_forever()

    with pytest.raises(TransferTimeoutError):
        await response_for(b"abc", recorder, timeouts)(HTTP_SCOPE, receive, send)

    assert recorder.closed == 1
