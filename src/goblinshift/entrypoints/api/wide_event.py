"""Evento amplo da requisição HTTP: o span `main = true` (docs/GoblinShiftPlan.md, 3.7).

O span vem da instrumentação do FastAPI; este módulo junta o que só a aplicação sabe e o grava
nele no fim da requisição:

- o middleware mede bytes recebidos e enviados, `duration_ms`, o primeiro byte e se a resposta
  terminou, e lê `request_id` e a classe de taxa;
- as rotas e dependências registram conta, credencial, código de erro e aceitação, pelas funções
  `record_*`, que não fazem nada fora de uma requisição;
- `count_queries` soma as consultas ao banco feitas durante a requisição.

O que nunca entra: conteúdo, nomes de arquivo, valores de parâmetros, query string, headers,
tokens e o `owner_id` (só o HMAC dele, `user.hash`).
"""

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from opentelemetry import trace
from opentelemetry.util.types import AttributeValue
from sqlalchemy import Engine, event
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from goblinshift.platform.telemetry import UserHasher

_MS_PER_SECOND = 1000.0
QUERY_STARTS_KEY = "goblinshift.query_starts"


class RateLimitClass(StrEnum):
    """Classes de rota dos limites de requisição de 1.7."""

    READ = "read"
    COMMAND = "command"
    RUN_CREATE = "run_create"
    # Envio e download de conteúdo: limitados por transferências simultâneas, não por minuto.
    TRANSFER = "transfer"


_READ_METHODS = frozenset({"GET", "HEAD"})
_ROUTE_CLASSES: dict[tuple[str, str], RateLimitClass] = {
    ("POST", "/api/v1/runs"): RateLimitClass.RUN_CREATE,
    ("PUT", "/api/v1/uploads/{upload_id}/content"): RateLimitClass.TRANSFER,
    ("GET", "/api/v1/artifacts/{artifact_id}/download"): RateLimitClass.TRANSFER,
}


def rate_limit_class(method: str, route_path: str) -> RateLimitClass:
    """Classe da rota (template, não a URL); leitura por método, salvo as exceções de 1.7."""
    explicit = _ROUTE_CLASSES.get((method, route_path))
    if explicit is not None:
        return explicit
    return RateLimitClass.READ if method in _READ_METHODS else RateLimitClass.COMMAND


@dataclass
class RequestEvent:
    """O que a requisição acumula até virar atributos do span."""

    started: float
    owner_id: UUID | None = None
    auth_via: str | None = None
    error_code: str | None = None
    bytes_received: int = 0
    bytes_sent: int = 0
    first_byte_ms: float | None = None
    completed: bool = False
    query_count: int = 0
    query_seconds: float = 0.0
    extra: dict[str, AttributeValue] = field(default_factory=dict)
    written: bool = False

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.started) * _MS_PER_SECOND


# Uma por requisição. As rotas síncronas rodam no threadpool com uma cópia do contexto, que
# aponta para o mesmo objeto; por isso o que elas registram aparece aqui.
_current: ContextVar[RequestEvent | None] = ContextVar("goblinshift_request_event", default=None)


def record_principal(owner_id: UUID, via: str) -> None:
    current = _current.get()
    if current is not None:
        current.owner_id = owner_id
        current.auth_via = via


def record_error(code: str) -> None:
    current = _current.get()
    if current is not None:
        current.error_code = code


def record_rate_limit(*, limited: bool) -> None:
    current = _current.get()
    if current is not None:
        current.extra["rate_limit.limited"] = limited


def record_run_accepted(
    run_id: UUID, recipe_id: str, recipe_version: int, *, replayed: bool
) -> None:
    current = _current.get()
    if current is not None:
        current.extra.update(
            {
                "run_id": str(run_id),
                "recipe_id": recipe_id,
                "recipe_version": recipe_version,
                "idempotency.replayed": replayed,
            }
        )


def count_queries(engine: Engine) -> None:
    """Conta as consultas e o tempo delas no evento da requisição em curso.

    Uma consulta que falha entra na contagem, mas não no tempo: o SQLAlchemy não chama
    `after_cursor_execute` quando o driver levanta erro.
    """

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn: Any, *_args: Any) -> None:
        current = _current.get()
        if current is None:
            return
        current.query_count += 1
        conn.info.setdefault(QUERY_STARTS_KEY, []).append(time.monotonic())

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn: Any, *_args: Any) -> None:
        current = _current.get()
        starts: list[float] = conn.info.get(QUERY_STARTS_KEY, [])
        if current is None or not starts:
            return
        current.query_seconds += time.monotonic() - starts.pop()

    @event.listens_for(engine, "handle_error")
    def _failed(context: Any) -> None:
        # A conexão volta ao pool com o `info` dela: sem isto, cada consulta que falha deixaria
        # um início para sempre na lista.
        conn = context.connection
        if _current.get() is None or conn is None:
            return
        starts: list[float] = conn.info.get(QUERY_STARTS_KEY, [])
        if starts:
            starts.pop()


class WideEventMiddleware:
    """Mede a requisição e grava o evento amplo no span da instrumentação.

    ASGI puro, como o `RequestContextMiddleware`, para não enfileirar respostas por streaming.
    Fica por fora dele, para ver o status e os bytes do 500 que ele produz.
    """

    def __init__(self, app: ASGIApp, hasher: UserHasher) -> None:
        self.app = app
        self.hasher = hasher

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        current = RequestEvent(started=time.monotonic())
        token = _current.set(current)

        async def counting_receive() -> Message:
            message = await receive()
            if message["type"] == "http.request":
                current.bytes_received += len(message.get("body", b""))
            return message

        async def counting_send(message: Message) -> None:
            if message["type"] == "http.response.start" and current.first_byte_ms is None:
                current.first_byte_ms = current.elapsed_ms()
            elif message["type"] == "http.response.body":
                current.bytes_sent += len(message.get("body", b""))
                if not message.get("more_body", False):
                    current.completed = True
                    # A instrumentação encerra o span ao repassar o último pedaço do corpo.
                    self._write(current, scope)
            await send(message)

        try:
            await self.app(scope, counting_receive, counting_send)
        finally:
            _current.reset(token)
            # Resposta interrompida: o span ainda está aberto e o evento sai daqui.
            self._write(current, scope)

    def _write(self, current: RequestEvent, scope: Scope) -> None:
        if current.written:
            return
        current.written = True
        span = trace.get_current_span()
        if not span.is_recording():
            return
        # O template da rota com o prefixo do roteador vem da instrumentação (`http.route`): o
        # FastAPI aninha os roteadores incluídos e o `scope["route"]` traz só o trecho final.
        route = (getattr(span, "attributes", None) or {}).get("http.route")
        span.set_attributes(self._attributes(current, scope, route))

    def _attributes(
        self, current: RequestEvent, scope: Scope, route: object
    ) -> dict[str, AttributeValue]:
        attributes: dict[str, AttributeValue] = {
            "duration_ms": current.elapsed_ms(),
            "http.request.body.size": current.bytes_received,
            "http.response.body.size": current.bytes_sent,
            "http.response.completed": current.completed,
            "db.query.count": current.query_count,
            "db.query.duration_ms": current.query_seconds * _MS_PER_SECOND,
            **current.extra,
        }
        request_id = scope.get("state", {}).get("request_id")
        if isinstance(request_id, str):
            attributes["request_id"] = request_id
        if isinstance(route, str):
            attributes["rate_limit.class"] = rate_limit_class(scope["method"], route).value
        if current.first_byte_ms is not None:
            attributes["http.response.first_byte_ms"] = current.first_byte_ms
        if current.owner_id is not None:
            attributes["user.hash"] = self.hasher.hash(current.owner_id)
        if current.auth_via is not None:
            attributes["auth.via"] = current.auth_via
        if current.error_code is not None:
            attributes["error.code"] = current.error_code
        return attributes
