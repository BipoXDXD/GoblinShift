"""Erros HTTP em problem+json (RFC 9457; docs/GoblinShiftPlan.md, 1.6 e 2.3).

Toda resposta de erro da API sai daqui, inclusive validação, rota inexistente, método não
permitido e exceção inesperada. Os módulos de rota levantam `ApiProblem(code, detail)`; o
status vem do catálogo, para que um `code` nunca apareça com dois status diferentes.

Campos: `type`, `title`, `status`, `detail`, `instance`, `code`, `request_id` e, na
validação, `errors` (lista de `{field, code, message}`).

- `type` é a URI estável do código: `https://goblinshift.dev/problems/<code-em-kebab>`, por
  exemplo `.../problems/body-too-large`. É um identificador, não precisa ser navegável.
- `instance` é o caminho da requisição, sem query string (que poderia carregar dados).
- `request_id` é um UUID gerado pelo servidor para cada requisição e ecoado no header
  `X-Request-ID`; um valor enviado pelo cliente é ignorado, para que o log não aceite
  identificadores forjados.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from goblinshift.entrypoints.api.wide_event import record_error

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://goblinshift.dev/problems/"
REQUEST_ID_HEADER = "X-Request-ID"


class ProblemCode(StrEnum):
    """Códigos HTTP do catálogo de 1.6, mais os genéricos que o catálogo não nomeia.

    Os genéricos (`BAD_REQUEST`, `AUTHENTICATION_REQUIRED`, `INSUFFICIENT_SCOPE`,
    `FORBIDDEN`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `REQUEST_TIMEOUT`, `STATE_CONFLICT`,
    `GONE`, `UNSUPPORTED_MEDIA_TYPE`, `INTERNAL_ERROR`, `SERVICE_UNAVAILABLE`) cobrem os
    status da tabela de erros de 1.6 que não tinham código próprio. Os códigos de item e de
    execução (`UNSUPPORTED_FORMAT`, `QUEUE_EXPIRED`...) não são respostas HTTP e ficam com
    quem os produz.
    """

    BAD_REQUEST = "BAD_REQUEST"
    JSON_INVALID = "JSON_INVALID"
    IDEMPOTENCY_KEY_INVALID = "IDEMPOTENCY_KEY_INVALID"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"
    FORBIDDEN = "FORBIDDEN"
    CSRF_FAILED = "CSRF_FAILED"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    REQUEST_TIMEOUT = "REQUEST_TIMEOUT"
    STATE_CONFLICT = "STATE_CONFLICT"
    IDEMPOTENCY_IN_PROGRESS = "IDEMPOTENCY_IN_PROGRESS"
    ACTIVE_RUN_LIMIT = "ACTIVE_RUN_LIMIT"
    GONE = "GONE"
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    INPUT_NOT_AVAILABLE = "INPUT_NOT_AVAILABLE"
    RECIPE_VERSION_RETIRED = "RECIPE_VERSION_RETIRED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    QUEUE_FULL = "QUEUE_FULL"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"

    @property
    def type_uri(self) -> str:
        return PROBLEM_TYPE_BASE + self.value.lower().replace("_", "-")


@dataclass(frozen=True)
class ProblemSpec:
    status: int
    title: str


PROBLEMS: Mapping[ProblemCode, ProblemSpec] = {
    ProblemCode.BAD_REQUEST: ProblemSpec(400, "Requisição inválida"),
    ProblemCode.JSON_INVALID: ProblemSpec(400, "JSON inválido"),
    ProblemCode.IDEMPOTENCY_KEY_INVALID: ProblemSpec(400, "Idempotency-Key inválida"),
    ProblemCode.AUTHENTICATION_REQUIRED: ProblemSpec(401, "Autenticação necessária"),
    ProblemCode.REAUTH_REQUIRED: ProblemSpec(401, "Autenticação recente necessária"),
    ProblemCode.INSUFFICIENT_SCOPE: ProblemSpec(403, "Escopo insuficiente"),
    ProblemCode.FORBIDDEN: ProblemSpec(403, "Acesso negado"),
    ProblemCode.CSRF_FAILED: ProblemSpec(403, "Verificação de CSRF recusada"),
    ProblemCode.NOT_FOUND: ProblemSpec(404, "Recurso não encontrado"),
    ProblemCode.METHOD_NOT_ALLOWED: ProblemSpec(405, "Método não permitido"),
    ProblemCode.REQUEST_TIMEOUT: ProblemSpec(408, "Transferência interrompida por tempo"),
    ProblemCode.STATE_CONFLICT: ProblemSpec(409, "Estado incompatível"),
    ProblemCode.IDEMPOTENCY_IN_PROGRESS: ProblemSpec(409, "Requisição idempotente em andamento"),
    ProblemCode.ACTIVE_RUN_LIMIT: ProblemSpec(409, "Já existe uma execução ativa"),
    ProblemCode.GONE: ProblemSpec(410, "Conteúdo expirado ou excluído"),
    ProblemCode.BODY_TOO_LARGE: ProblemSpec(413, "Corpo grande demais"),
    ProblemCode.UNSUPPORTED_MEDIA_TYPE: ProblemSpec(415, "Tipo de mídia não suportado"),
    ProblemCode.VALIDATION_FAILED: ProblemSpec(422, "Validação falhou"),
    ProblemCode.IDEMPOTENCY_KEY_REUSED: ProblemSpec(422, "Idempotency-Key reutilizada"),
    ProblemCode.INPUT_NOT_AVAILABLE: ProblemSpec(422, "Entrada indisponível"),
    ProblemCode.RECIPE_VERSION_RETIRED: ProblemSpec(422, "Versão de receita retirada"),
    ProblemCode.QUOTA_EXCEEDED: ProblemSpec(429, "Cota excedida"),
    ProblemCode.RATE_LIMITED: ProblemSpec(429, "Muitas requisições"),
    ProblemCode.INTERNAL_ERROR: ProblemSpec(500, "Erro interno"),
    ProblemCode.QUEUE_FULL: ProblemSpec(503, "Fila cheia"),
    ProblemCode.SERVICE_UNAVAILABLE: ProblemSpec(503, "Serviço indisponível"),
}

# Status que o roteador, o Starlette e o FastAPI produzem sozinhos, sem passar por ApiProblem.
# Cada um aponta para o código genérico do mesmo status no catálogo; um status fora da tabela
# cai no genérico da classe (`code_for_http_status`).
_HTTP_STATUS_CODES: Mapping[int, ProblemCode] = {
    400: ProblemCode.BAD_REQUEST,
    401: ProblemCode.AUTHENTICATION_REQUIRED,
    403: ProblemCode.FORBIDDEN,
    404: ProblemCode.NOT_FOUND,
    405: ProblemCode.METHOD_NOT_ALLOWED,
    408: ProblemCode.REQUEST_TIMEOUT,
    409: ProblemCode.STATE_CONFLICT,
    410: ProblemCode.GONE,
    413: ProblemCode.BODY_TOO_LARGE,
    415: ProblemCode.UNSUPPORTED_MEDIA_TYPE,
    422: ProblemCode.VALIDATION_FAILED,
    429: ProblemCode.RATE_LIMITED,
    500: ProblemCode.INTERNAL_ERROR,
    503: ProblemCode.SERVICE_UNAVAILABLE,
}
_HTTP_DETAILS: Mapping[ProblemCode, str] = {
    ProblemCode.NOT_FOUND: "A URL não corresponde a nenhum recurso.",
    ProblemCode.METHOD_NOT_ALLOWED: "O recurso não aceita este método; veja o header Allow.",
}
_FORWARDED_HEADERS = ("Allow", "Retry-After", "WWW-Authenticate")
_SERVER_ERROR_MIN = 500


class FieldError(BaseModel):
    field: str
    code: str
    message: str


class ProblemDetails(BaseModel):
    """Schema publicado no OpenAPI; as respostas são montadas por `problem_response`."""

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: ProblemCode
    request_id: str
    errors: list[FieldError] | None = None


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Respostas problem+json para o OpenAPI da rota (2.3)."""
    return {
        status: {"model": ProblemDetails, "content": {PROBLEM_MEDIA_TYPE: {}}}
        for status in statuses
    }


# Respostas de erro comuns a toda rota autenticada.
PROBLEM_RESPONSES = problem_responses(401, 403, 422, 500)


# O nome segue o termo "problem" da RFC 9457, que é o que a classe representa.
class ApiProblem(Exception):  # noqa: N818
    """Erro que uma rota devolve ao cliente; o status sai do catálogo pelo `code`."""

    def __init__(
        self,
        code: ProblemCode,
        detail: str,
        *,
        headers: Mapping[str, str] | None = None,
        errors: Sequence[Mapping[str, str]] | None = None,
    ) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.headers: Mapping[str, str] = dict(headers or {})
        # Erros por campo (`field`, `code`, `message`), como os da validação do Pydantic.
        self.errors = errors

    @property
    def status(self) -> int:
        return PROBLEMS[self.code].status


def install_problem_handlers(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)
    app.exception_handler(ApiProblem)(_handle_api_problem)
    app.exception_handler(RequestValidationError)(_handle_validation_error)
    app.exception_handler(StarletteHTTPException)(_handle_http_exception)


def problem_response(
    *,
    code: ProblemCode,
    detail: str,
    instance: str,
    request_id: str,
    headers: Mapping[str, str] | None = None,
    errors: Sequence[Mapping[str, str]] | None = None,
) -> JSONResponse:
    spec = PROBLEMS[code]
    record_error(code.value)
    body: dict[str, Any] = {
        "type": code.type_uri,
        "title": spec.title,
        "status": spec.status,
        "detail": detail,
        "instance": instance,
        "code": code.value,
        "request_id": request_id,
    }
    if errors is not None:
        body["errors"] = list(errors)
    return JSONResponse(
        body,
        status_code=spec.status,
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
        media_type=PROBLEM_MEDIA_TYPE,
    )


class RequestContextMiddleware:
    """Gera o `request_id` e transforma exceção inesperada em 500 genérico.

    ASGI puro, e não `BaseHTTPMiddleware`, para não enfileirar o corpo das respostas por
    streaming. A exceção é registrada no log com o `request_id`; o cliente recebe só o
    código, sem traceback, SQL nem nome de classe.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("erro inesperado na requisição %s", request_id)
            # Com a resposta já começada não há como trocar o status; a conexão é cortada.
            if response_started:
                record_error(ProblemCode.INTERNAL_ERROR.value)
                raise
            response = problem_response(
                code=ProblemCode.INTERNAL_ERROR,
                detail=f"Erro interno. Informe o request_id {request_id} ao suporte.",
                instance=scope["path"],
                request_id=request_id,
            )
            await response(scope, receive, send_with_request_id)


def request_id_of(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if not isinstance(request_id, str):
        raise RuntimeError("RequestContextMiddleware não está instalado na aplicação")
    return request_id


async def _handle_api_problem(request: Request, exc: ApiProblem) -> JSONResponse:
    return problem_response(
        code=exc.code,
        detail=exc.detail,
        instance=request.url.path,
        request_id=request_id_of(request),
        headers=exc.headers,
        errors=exc.errors,
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return problem_response(
        code=ProblemCode.VALIDATION_FAILED,
        detail="A requisição tem campos inválidos; veja `errors`.",
        instance=request.url.path,
        request_id=request_id_of(request),
        errors=[_field_error(error) for error in exc.errors()],
    )


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = code_for_http_status(exc.status_code)
    # O detalhe do Starlette não vai para o cliente: só textos fixos, escritos aqui.
    detail = _HTTP_DETAILS.get(code, PROBLEMS[code].title)
    headers = {
        name: value
        for name, value in (exc.headers or {}).items()
        if name.title() in _FORWARDED_HEADERS
    }
    return problem_response(
        code=code,
        detail=detail,
        instance=request.url.path,
        request_id=request_id_of(request),
        headers=headers,
    )


def code_for_http_status(status: int) -> ProblemCode:
    """Código do catálogo para o status de uma `HTTPException` do framework.

    A resposta sai sempre com o status do código (1.6), nunca com o original: um status sem
    código próprio vira `BAD_REQUEST` (400) ou `INTERNAL_ERROR` (500). O framework não os emite;
    só chegariam aqui por um `raise HTTPException` avulso, e manter o par código/status do
    catálogo vale mais que ecoar um status fora dele.
    """
    return _HTTP_STATUS_CODES.get(status) or (
        ProblemCode.INTERNAL_ERROR if status >= _SERVER_ERROR_MIN else ProblemCode.BAD_REQUEST
    )


def _field_error(error: Mapping[str, Any]) -> dict[str, str]:
    # Só local, tipo e mensagem: `input` e `ctx` podem ecoar o que o cliente mandou.
    return {
        "field": ".".join(str(part) for part in error["loc"]),
        "code": str(error["type"]),
        "message": str(error["msg"]),
    }
