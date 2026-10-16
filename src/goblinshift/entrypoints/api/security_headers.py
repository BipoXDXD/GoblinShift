"""Cabeçalhos de segurança em toda resposta da API (docs/GoblinShiftPlan.md, 3.6; Frente B).

Middleware ASGI puro: `BaseHTTPMiddleware` quebraria o streaming dos downloads e os contextvars
do OpenTelemetry. Mexe só na mensagem `http.response.start`; o corpo passa intacto. Não
sobrescreve nem duplica um header que a rota já definiu (o download tem a sua CSP).

A CSP da SPA (`script-src 'self'`…) é do Nginx, que serve os estáticos (fatia 7). As respostas da
API não são documentos: recebem `default-src 'none'`. A exceção é o HTML da `/docs`, cujo Swagger
UI carrega script e estilo e quebraria com essa CSP.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CSP = "content-security-policy"
_HTML = "text/html"
_API_CSP = "default-src 'none'; frame-ancestors 'none'"
_HSTS = "max-age=63072000; includeSubDomains"
# `no-store` em tudo: as rotas públicas são sondas e documentação, e nada ganham com cache, e as
# autenticadas não podem ficar em cache compartilhado nem no disco do navegador.
_COMMON_HEADERS = (
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
    ("cross-origin-opener-policy", "same-origin"),
    # Nenhuma resposta da API é recurso para outro site embutir (img, script, fetch no-cors). O
    # download baixado pela própria origem, por navegação ou fetch, não é afetado.
    ("cross-origin-resource-policy", "same-origin"),
    ("permissions-policy", "camera=(), microphone=(), geolocation=()"),
    ("cache-control", "no-store"),
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self._app = app
        # HSTS só em produção: em http://localhost ele prenderia o navegador ao HTTPS.
        self._defaults = _COMMON_HEADERS + ((("strict-transport-security", _HSTS),) if hsts else ())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                self._add_missing(MutableHeaders(scope=message))
            await send(message)

        await self._app(scope, receive, send_with_headers)

    def _add_missing(self, headers: MutableHeaders) -> None:
        # MutableHeaders compara nomes sem diferenciar maiúsculas.
        for name, value in self._defaults:
            if name not in headers:
                headers.append(name, value)
        is_document = headers.get("content-type", "").startswith(_HTML)
        if _CSP not in headers and not is_document:
            headers.append(_CSP, _API_CSP)
