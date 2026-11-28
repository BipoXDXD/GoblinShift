"""Transport do httpx para as chamadas aos provedores de login: teto de bytes por resposta.

Token, JWKS e `/user` são respostas pequenas. Sem teto, um provedor hostil (ou quebrado) faz o
callback ler megabytes para a memória antes de qualquer validação. O `CappedTransport` recusa um
`Content-Length` acima do teto antes de ler e, sem ele, para de ler no primeiro bloco que passa
do teto. Resposta comprimida é recusada: o httpx a descomprime depois do transport, então o teto
contaria os bytes comprimidos e um corpo pequeno viraria megabytes. O prazo total do callback
fica em `auth_login`.
"""

from collections.abc import AsyncIterator
from typing import cast

import httpx2

from goblinshift.identity.providers import MAX_GITHUB_USER_BYTES

# O mesmo teto do `/user` do GitHub, a maior das respostas esperadas (token e JWKS são menores).
MAX_PROVIDER_RESPONSE_BYTES = MAX_GITHUB_USER_BYTES


class ProviderResponseTooLargeError(httpx2.HTTPError):
    """Resposta do provedor acima do teto (ou comprimida, sem teto possível); o login a trata
    como qualquer falha de rede."""


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
    derrubaria a conexão de outro login em andamento, então o `aclose` daqui não fecha o pool:
    quem fecha o transport de dentro é o lifespan da aplicação, na parada.
    """

    def __init__(self, inner: httpx2.AsyncBaseTransport, limit: int) -> None:
        self._inner = inner
        self._limit = limit

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        # O teto vale para os bytes que chegam aqui; o httpx descomprime só depois, então a
        # resposta precisa vir sem compressão: pedida assim e conferida, porque o pedido não obriga.
        request.headers["Accept-Encoding"] = "identity"
        response = await self._inner.handle_async_request(request)
        if _is_compressed(response):
            await response.aclose()
            raise ProviderResponseTooLargeError("resposta do provedor comprimida")
        if _declared_length(response) > self._limit:
            await response.aclose()
            raise ProviderResponseTooLargeError("resposta do provedor acima do teto")
        # A resposta de um transport assíncrono traz sempre um `AsyncByteStream`.
        stream = cast(httpx2.AsyncByteStream, response.stream)
        response.stream = _CappedStream(stream, self._limit)
        return response

    async def aclose(self) -> None:
        return


def _is_compressed(response: httpx2.Response) -> bool:
    encoding = response.headers.get("Content-Encoding")
    return encoding is not None and encoding.strip().lower() != "identity"


def _declared_length(response: httpx2.Response) -> int:
    declared = response.headers.get("Content-Length", "")
    return int(declared) if declared.isdigit() else 0
