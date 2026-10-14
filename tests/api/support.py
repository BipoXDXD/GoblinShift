"""Rotas de teste e conferência de problem+json para os testes da API."""

import contextlib
from collections.abc import Iterator
from typing import Annotated, Any
from uuid import UUID

import httpx2 as httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from starlette.exceptions import HTTPException as StarletteHTTPException

from goblinshift.entrypoints.api.dependencies import require_scope
from goblinshift.entrypoints.api.json_command import json_command, read_json_command
from goblinshift.entrypoints.api.problems import PROBLEMS, ProblemCode
from goblinshift.entrypoints.api.wide_event import QUERY_STARTS_KEY
from goblinshift.identity import Principal, Scope

PROBLEM_KEYS = {"type", "title", "status", "detail", "instance", "code", "request_id"}


class StreamInterruptedError(Exception):
    pass


class EchoCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


def add_test_routes(app: FastAPI) -> None:
    router = APIRouter(prefix="/api/v1/_test")

    @router.post("/echo")
    def echo(command: Annotated[EchoCommand, Depends(json_command(EchoCommand))]) -> dict[str, int]:
        return {"text_length": len(command.text)}

    @router.post("/raw")
    def raw(value: Annotated[object, Depends(read_json_command)]) -> dict[str, Any]:
        return {"value": value}

    @router.get("/items")
    def items(limit: int) -> dict[str, int]:
        return {"limit": limit}

    @router.get("/http-error/{status}")
    def http_error(status: int) -> None:
        raise StarletteHTTPException(status_code=status, headers={"Retry-After": "7"})

    @router.get("/boom")
    def boom() -> None:
        raise ProgrammingError("SELECT secret_hash FROM api_key", {}, Exception("conexão caiu"))

    @router.get("/stream-boom")
    def stream_boom() -> StreamingResponse:
        def chunks() -> Iterator[bytes]:
            yield b"primeiro pedaco"
            raise StreamInterruptedError("o disco sumiu no meio do download")

        return StreamingResponse(chunks())

    @router.get("/failed-query")
    def failed_query(request: Request) -> dict[str, int]:
        """Consulta que o PostgreSQL recusa; devolve os inícios de consulta que sobraram."""
        engine: Engine = request.app.state.engine
        with engine.connect() as conn:
            with contextlib.suppress(DBAPIError):
                conn.execute(text("SELECT CAST('não é uuid' AS uuid)"))
            return {"pending_query_starts": len(conn.info.get(QUERY_STARTS_KEY, []))}

    @router.get("/files-write")
    def files_write(
        principal: Annotated[Principal, Depends(require_scope(Scope.FILES_WRITE))],
    ) -> dict[str, UUID]:
        return {"owner_id": principal.owner_id}

    app.include_router(router)


def assert_problem(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Confere o envelope RFC 9457 e devolve o corpo para os asserts específicos."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body: dict[str, Any] = response.json()
    assert PROBLEM_KEYS <= body.keys() <= PROBLEM_KEYS | {"errors"}
    assert body["status"] == status
    assert body["code"] == code
    # Um `code` nunca sai com status diferente do catálogo (1.6).
    assert PROBLEMS[ProblemCode(code)].status == status
    assert body["type"] == "https://goblinshift.dev/problems/" + code.lower().replace("_", "-")
    assert body["request_id"] == response.headers["x-request-id"]
    UUID(body["request_id"])
    return body
