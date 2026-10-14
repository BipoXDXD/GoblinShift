"""Decodificação protegida dos comandos JSON (docs/GoblinShiftPlan.md, 1.7 e 3.6).

O corpo declarado como modelo Pydantic numa rota seria decodificado pelo `json` padrão, que
fica com a última de duas chaves iguais, aceita `NaN` e `Infinity` e não limita tamanho nem
profundidade. Por isso as rotas de comando recebem o corpo por esta dependência:

1. `Content-Type` precisa ser `application/json` (415);
2. o corpo é lido por `request.stream()`, contando bytes, e passa de 64 KiB → 413, sem
   confiar em `Content-Length`;
3. UTF-8 estrito, sem chave duplicada, sem `NaN`/`Infinity` nem número que estoura para
   infinito, e profundidade de no máximo 16 → senão 400 `JSON_INVALID`;
4. só então o Pydantic valida em modo estrito, com campos extras proibidos pelo modelo (422).

Uso numa rota, com o schema publicado no OpenAPI por `command_openapi` (3.6):

    @router.post("/coisas", openapi_extra=command_openapi(NewThing))
    def create(command: Annotated[NewThing, Depends(json_command(NewThing))]) -> ...:
"""

import json
import math
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode

MAX_COMMAND_BYTES = 64 * 1024
MAX_COMMAND_DEPTH = 16
JSON_MEDIA_TYPE = "application/json"
_DEFS_PREFIX = "#/$defs/"


class _JsonRejectedError(ValueError):
    pass


def json_command[M: BaseModel](model: type[M]) -> Callable[[Request], Awaitable[M]]:
    """Dependência que devolve o comando já validado como `model`."""

    async def read_command(request: Request) -> M:
        body = await read_command_body(request)
        decode_json_command(body)
        try:
            # Valida os mesmos bytes no modo JSON do Pydantic: no modo Python, o estrito
            # recusaria um UUID ou uma data escritos como texto, que é como chegam em JSON.
            return model.model_validate_json(body, strict=True)
        except ValidationError as error:
            raise RequestValidationError(
                [{**detail, "loc": ("body", *detail["loc"])} for detail in error.errors()]
            ) from None

    return read_command


def command_openapi(model: type[BaseModel]) -> dict[str, Any]:
    """`openapi_extra` que publica o corpo do comando, que a rota não declara como parâmetro.

    Os modelos aninhados ficam embutidos: o `$ref` para `#/$defs/...` gerado pelo Pydantic
    resolveria contra a raiz do documento OpenAPI, onde esses `$defs` não existem.
    """
    schema = model.model_json_schema()
    definitions: dict[str, Any] = schema.pop("$defs", {})
    return {
        "requestBody": {
            "required": True,
            "content": {JSON_MEDIA_TYPE: {"schema": _inline_refs(schema, definitions)}},
        }
    }


async def read_json_command(request: Request) -> object:
    """Dependência que devolve o JSON decodificado, para a rota validar por conta própria."""
    return decode_json_command(await read_command_body(request))


async def read_command_body(request: Request) -> bytes:
    _require_json_media_type(request)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_COMMAND_BYTES:
            raise ApiProblem(
                ProblemCode.BODY_TOO_LARGE,
                f"O comando passa de {MAX_COMMAND_BYTES} bytes.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def decode_json_command(body: bytes) -> object:
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
            parse_float=_parse_finite_float,
        )
    except _JsonRejectedError as error:
        raise ApiProblem(ProblemCode.JSON_INVALID, str(error)) from None
    except UnicodeDecodeError, ValueError:
        raise ApiProblem(ProblemCode.JSON_INVALID, "O corpo não é JSON válido em UTF-8.") from None
    except RecursionError:
        # O decodificador em C desiste antes de esgotar a pilha em aninhamentos extremos.
        raise _too_deep() from None
    if _depth(value) > MAX_COMMAND_DEPTH:
        raise _too_deep()
    return value


def _require_json_media_type(request: Request) -> None:
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if media_type != JSON_MEDIA_TYPE:
        raise ApiProblem(
            ProblemCode.UNSUPPORTED_MEDIA_TYPE,
            f"Comandos são enviados como {JSON_MEDIA_TYPE}.",
        )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise _JsonRejectedError("O JSON tem chaves repetidas num mesmo objeto.")
    return result


def _reject_constant(name: str) -> object:
    raise _JsonRejectedError(f"{name} não é um número JSON aceito.")


def _parse_finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _JsonRejectedError("O JSON tem um número fora da faixa de ponto flutuante.")
    return value


def _depth(value: object) -> int:
    """Quantos objetos e arrays aninhados há no caminho mais fundo; escalar conta 0."""
    deepest = 0
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending:
        current, level = pending.pop()
        if isinstance(current, dict):
            children: list[object] = list(current.values())
        elif isinstance(current, list):
            children = current
        else:
            continue
        deepest = max(deepest, level)
        pending.extend((child, level + 1) for child in children)
    return deepest


def _inline_refs(node: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_DEFS_PREFIX):
            return _inline_refs(definitions[ref.removeprefix(_DEFS_PREFIX)], definitions)
        return {key: _inline_refs(value, definitions) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline_refs(item, definitions) for item in node]
    return node


def _too_deep() -> ApiProblem:
    return ApiProblem(
        ProblemCode.JSON_INVALID,
        f"O JSON passa da profundidade máxima de {MAX_COMMAND_DEPTH}.",
    )
