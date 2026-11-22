"""Teto de requisições simultâneas da API, recusado em problem+json com Retry-After (2.3).

O uvicorn tem o próprio teto (`--limit-concurrency`, docker/app.Dockerfile), mas responde 503
`text/plain`, sem Retry-After, antes de o pedido chegar ao ASGI. Este middleware recusa um pouco
antes, no formato do contrato; o teto do uvicorn fica como última barreira.
"""

import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from goblinshift.entrypoints.api.problems import ProblemCode, problem_response

# Abaixo das 29 conexões que o uvicorn atende (`--limit-concurrency 30`, que compara com `>=`),
# com folga para o /livez, que não conta. tests/test_api_server_limits.py confere a conta.
MAX_CONCURRENT_REQUESTS = 28
# Um pedido termina e a vaga volta; o menor prazo que o cabeçalho expressa.
SATURATED_RETRY_AFTER_SECONDS = 1
# As sondas respondem mesmo com a API saturada: saturada não é morta. O /readyz tem cache de 1 s e
# zona própria no Nginx, então não pesa; recusado, o healthcheck tiraria do ar uma API viva.
_EXEMPT_PATHS = frozenset({"/livez", "/readyz"})
# Uma linha por recusa, que o sinal de saturação (saturation.py) soma às recusas do uvicorn: o
# middleware recusa antes dele, e sem isto o alerta `causa-api-saturada` ficaria cego.
SATURATED_LOGGER = __name__
SATURATED_LOG_MESSAGE = "teto de requisições simultâneas atingido"
logger = logging.getLogger(SATURATED_LOGGER)
_DETAIL = "A API está no limite de requisições simultâneas; tente de novo depois do Retry-After."


class ConcurrencyLimitMiddleware:
    """Conta as requisições HTTP em curso e recusa as que passam de `limit`.

    ASGI puro: a vaga vale até o fim da resposta, inclusive a de streaming. O contador é um
    inteiro do event loop, conferido e incrementado sem `await` no meio, então não precisa de
    trava. Fica dentro do `RequestContextMiddleware`, que já pôs o `request_id` no escopo.
    """

    def __init__(self, app: ASGIApp, *, limit: int) -> None:
        self.app = app
        self.limit = limit
        self.active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        if self.active >= self.limit:
            logger.warning(SATURATED_LOG_MESSAGE)
            response = problem_response(
                code=ProblemCode.SERVICE_UNAVAILABLE,
                detail=_DETAIL,
                instance=scope["path"],
                request_id=scope["state"]["request_id"],
                headers={"Retry-After": str(SATURATED_RETRY_AFTER_SECONDS)},
            )
            await response(scope, receive, send)
            return
        self.active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.active -= 1
