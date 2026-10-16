"""`GET /api/v1/catalog` e o detalhe de uma versão: limites, defaults e schema (1.6).

Qualquer chave válida lê o catálogo (tabela de rotas de 1.6): ele não tem dado de conta. A
resposta só muda com uma versão nova do código, então vai com `ETag` forte e `no-cache`: o
cliente guarda e revalida a cada uso, e o `304` poupa o corpo. `private` porque a rota é
autenticada e nenhum cache compartilhado deve guardá-la.

O detalhe (`/catalog/{recipe_id}/versions/{version}`) responde também por versões retiradas, que
saem da listagem mas continuam legíveis para o histórico (1.6). Versão `deprecated` leva o header
`Sunset` (RFC 8594), assim como as demais respostas que a usam (`deprecation_headers`).
"""

import hashlib
import json
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, Response
from pydantic import BaseModel

from goblinshift.catalog import (
    CATALOG,
    Catalog,
    RecipeVersion,
    UnknownRecipeVersionError,
    VersionStatus,
)
from goblinshift.entrypoints.api.dependencies import CurrentPrincipal
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode, problem_responses

router = APIRouter()

CATALOG_PATH = "/catalog"
JSON_MEDIA_TYPE = "application/json"
CACHE_CONTROL = "private, no-cache"
SUNSET_HEADER = "Sunset"
MAX_RECIPE_ID_CHARS = 64
# Formato dos ids do catálogo (`csv-janitor`): caminho e filtros só aceitam ids nesse formato.
RECIPE_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
# No máximo duas versões convivem (1.6); o teto só limita a entrada, longe do que existe.
MAX_RECIPE_VERSION = 999


class RecipeVersionResponse(BaseModel):
    version: int
    status: VersionStatus
    limits: dict[str, int]
    defaults: dict[str, Any]
    parameters_schema: dict[str, Any]


class RecipeResponse(BaseModel):
    recipe_id: str
    versions: list[RecipeVersionResponse]


class CatalogResponse(BaseModel):
    recipes: list[RecipeResponse]


class RecipeVersionDetailResponse(RecipeVersionResponse):
    recipe_id: str
    # Data a partir da qual a versão deixa de ser aceita; só existe em `deprecated` e `retired`.
    sunset_at: datetime | None


def catalog_response(catalog: Catalog) -> CatalogResponse:
    """Versões aceitas (não retiradas), agrupadas por receita na ordem do catálogo."""
    by_recipe: dict[str, list[RecipeVersionResponse]] = {}
    for version in catalog.list_versions():
        if version.status is not VersionStatus.RETIRED:
            by_recipe.setdefault(version.recipe_id, []).append(_version_response(version))
    return CatalogResponse(
        recipes=[
            RecipeResponse(recipe_id=recipe_id, versions=versions)
            for recipe_id, versions in by_recipe.items()
        ]
    )


def _version_response(version: RecipeVersion) -> RecipeVersionResponse:
    return RecipeVersionResponse(
        version=version.version,
        status=version.status,
        limits=dict(version.limits),
        defaults=version.resolve({}),
        parameters_schema=version.parameters_schema(),
    )


def version_detail_response(version: RecipeVersion) -> RecipeVersionDetailResponse:
    sunset_at = version.sunset_at.astimezone(UTC) if version.sunset_at is not None else None
    return RecipeVersionDetailResponse(
        recipe_id=version.recipe_id,
        **_version_response(version).model_dump(),
        sunset_at=sunset_at,
    )


def recipe_command_schema(common: dict[str, Any]) -> dict[str, Any]:
    """Corpo de `POST /runs` e dos presets: `allOf` (campos comuns) + `oneOf` das versões (1.6).

    `unevaluatedProperties: false` fica no nível do `allOf`, o único em que o JSON Schema
    2020-12 enxerga as propriedades avaliadas pelas duas partes (ver `catalog.versions`).
    """
    variants = [
        version.variant_schema()
        for version in CATALOG.list_versions()
        if version.status is not VersionStatus.RETIRED
    ]
    return {"allOf": [common, {"oneOf": variants}], "unevaluatedProperties": False}


def deprecation_headers(version: RecipeVersion) -> dict[str, str]:
    """`Sunset` em HTTP-date (RFC 8594) nas respostas que usam uma versão `deprecated`."""
    if version.status is not VersionStatus.DEPRECATED or version.sunset_at is None:
        return {}
    return {SUNSET_HEADER: format_datetime(version.sunset_at.astimezone(UTC), usegmt=True)}


def _json_bytes(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


def _cache_headers(body: bytes) -> dict[str, str]:
    return {"ETag": f'"{hashlib.sha256(body).hexdigest()[:32]}"', "Cache-Control": CACHE_CONTROL}


# O catálogo é fixo no processo: o corpo da listagem e o ETag são calculados uma vez, na
# importação. O detalhe é montado a cada pedido: é uma versão só, e barato.
_BODY = _json_bytes(catalog_response(CATALOG).model_dump(mode="json"))
_HEADERS = _cache_headers(_BODY)


@router.get(
    CATALOG_PATH,
    operation_id="getCatalog",
    response_model=CatalogResponse,
    responses={304: {"description": "O `ETag` enviado em `If-None-Match` continua válido."}},
)
def get_catalog(_principal: CurrentPrincipal, request: Request) -> Response:
    return _cacheable(request, _BODY, _HEADERS)


@router.get(
    f"{CATALOG_PATH}/{{recipe_id}}/versions/{{version}}",
    operation_id="getRecipeVersion",
    response_model=RecipeVersionDetailResponse,
    responses={
        304: {"description": "O `ETag` enviado em `If-None-Match` continua válido."},
        **problem_responses(404),
    },
)
def get_recipe_version(
    _principal: CurrentPrincipal,
    request: Request,
    recipe_id: Annotated[str, Path(max_length=MAX_RECIPE_ID_CHARS, pattern=RECIPE_ID_PATTERN)],
    version: Annotated[int, Path(ge=1, le=MAX_RECIPE_VERSION)],
) -> Response:
    try:
        recipe_version = CATALOG.get_version(recipe_id, version)
    except UnknownRecipeVersionError:
        raise ApiProblem(ProblemCode.NOT_FOUND, "Versão de receita não encontrada.") from None
    body = _json_bytes(version_detail_response(recipe_version).model_dump(mode="json"))
    headers = {**_cache_headers(body), **deprecation_headers(recipe_version)}
    return _cacheable(request, body, headers)


def _cacheable(request: Request, body: bytes, headers: dict[str, str]) -> Response:
    if _etag_matches(request.headers.getlist("if-none-match"), headers["ETag"]):
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type=JSON_MEDIA_TYPE, headers=headers)


def _etag_matches(if_none_match: list[str], etag: str) -> bool:
    """Comparação fraca do `If-None-Match` (RFC 9110, 13.1.2): `*` ou a lista de tags."""
    tags = [tag.strip() for header in if_none_match for tag in header.split(",")]
    return any(tag == "*" or tag.removeprefix("W/") == etag for tag in tags)
