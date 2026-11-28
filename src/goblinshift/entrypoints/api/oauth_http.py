"""Transport do httpx para as chamadas aos provedores de login: teto de bytes por resposta.

Token, JWKS e `/user` são respostas pequenas. Sem teto, um provedor hostil (ou quebrado) faz o
callback ler megabytes para a memória antes de qualquer validação. O `CappedTransport` recusa um
`Content-Length` acima do teto antes de ler e, sem ele, para de ler no primeiro bloco que passa
do teto. O prazo total do callback fica em `auth_login`.
"""

from collections.abc import AsyncIterator
from typing import cast

import httpx2

MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024


class ProviderResponseTooLargeError(httpx2.HTTPError):
    """Resposta do provedor maior que o teto; o login a trata como qualquer falha de rede."""


class _CappedStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream, limit: int) -> None:
        self._stream = stream
        self._limit = limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        received = 0
        async for chunk in self._stream:
            received += len(chunk)
            if received > self._limit:
                raise ProviderResponseTooLargeError("resposta do provedor acima do teto")
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class CappedTransport(httpx2.AsyncBaseTransport):
    """Embrulha o transport real e limita o corpo de cada resposta a `limit` bytes.

    O Authlib cria e fecha um cliente por chamada, todos com este mesmo transport: fechá-lo
    derrubaria a conexão de outro login em andamento, então o pool vive o processo inteiro.
    """

    def __init__(self, inner: httpx2.AsyncBaseTransport, limit: int) -> None:
        self._inner = inner
        self._limit = limit

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        # Sem compressão: o teto conta os bytes que de fato viram memória.
        request.headers["Accept-Encoding"] = "identity"
        response = await self._inner.handle_async_request(request)
        if _declared_length(response) > self._limit:
            await response.aclose()
            raise ProviderResponseTooLargeError("resposta do provedor acima do teto")
        # A resposta de um transport assíncrono traz sempre um `AsyncByteStream`.
        stream = cast(httpx2.AsyncByteStream, response.stream)
        response.stream = _CappedStream(stream, self._limit)
        return response

    async def aclose(self) -> None:
        return


def _declared_length(response: httpx2.Response) -> int:
    declared = response.headers.get("Content-Length", "")
    return int(declared) if declared.isdigit() else 0
