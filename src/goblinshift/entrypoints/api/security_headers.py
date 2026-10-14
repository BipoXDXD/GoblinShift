"""Cabeçalhos de segurança em toda resposta da API (docs/GoblinShiftPlan.md, 3.6; Frente B).

Middleware ASGI puro: `BaseHTTPMiddleware` quebraria o streaming dos downloads e os contextvars
do OpenTelemetry. Não sobrescreve um header que a rota já definiu (o download tem a sua CSP).

Instalado pelo `app.py` na Fase 0 da fatia 6, ainda sem cabeçalhos; a Frente B os acrescenta.
"""

from starlette.types import ASGIApp, Receive, Scope, Send


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self._app = app
        # HSTS só em produção: em http://localhost ele prenderia o navegador ao HTTPS.
        self._hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._app(scope, receive, send)
