"""`GET /api/v1/catalog`: receitas, versões aceitas, limites, defaults e schema (1.6).

Qualquer chave válida lê o catálogo (tabela de rotas de 1.6): ele não tem dado de conta. A
resposta só muda com uma versão nova do código, então vai com `ETag` forte e `no-cache`: o
cliente guarda e revalida a cada uso, e o `304` poupa o corpo. `private` porque a rota é
autenticada e nenhum cache compartilhado deve guardá-la.
"""

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from goblinshift.catalog import CATALOG, Catalog, RecipeVersion, VersionStatus
from goblinshift.entrypoints.api.dependencies import CurrentPrincipal

router = APIRouter()

CATALOG_PATH = "/catalog"
JSON_MEDIA_TYPE = "application/json"
CACHE_CONTROL = "private, no-cache"


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


# O catálogo é fixo no processo: corpo e ETag são calculados uma vez, na importação.
_BODY = json.dumps(
    catalog_response(CATALOG).model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
).encode()
_ETAG = f'"{hashlib.sha256(_BODY).hexdigest()[:32]}"'
_HEADERS = {"ETag": _ETAG, "Cache-Control": CACHE_CONTROL}


@router.get(
    CATALOG_PATH,
    operation_id="getCatalog",
    response_model=CatalogResponse,
    responses={304: {"description": "O `ETag` enviado em `If-None-Match` continua válido."}},
)
def get_catalog(_principal: CurrentPrincipal, request: Request) -> Response:
    if _etag_matches(request.headers.getlist("if-none-match")):
        return Response(status_code=304, headers=_HEADERS)
    return Response(content=_BODY, media_type=JSON_MEDIA_TYPE, headers=_HEADERS)


def _etag_matches(if_none_match: list[str]) -> bool:
    """Comparação fraca do `If-None-Match` (RFC 9110, 13.1.2): `*` ou a lista de tags."""
    tags = [tag.strip() for header in if_none_match for tag in header.split(",")]
    return any(tag == "*" or tag.removeprefix("W/") == _ETAG for tag in tags)
