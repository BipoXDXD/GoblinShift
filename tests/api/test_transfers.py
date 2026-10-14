"""Peças HTTP das transferências, sem banco: nome do anexo e encerramento do download."""

import io
from datetime import timedelta
from typing import Any

import anyio
import pytest
from starlette.requests import ClientDisconnect
from starlette.types import Message

from goblinshift.entrypoints.api.transfers import (
    DownloadResponse,
    TransferTimeoutError,
    TransferTimeouts,
    content_disposition,
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
