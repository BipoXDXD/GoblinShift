"""Rotas de presets: configurações privadas com revisão (docs/GoblinShiftPlan.md, 1.6 e 3.6).

- `POST`: corpo estrito, igual ao de `POST /runs` sem as entradas (`name` no lugar de
  `input_upload_ids`); `201` com `Location` e `ETag`. Nome repetido ou acima de 100 presets
  na conta → `409`.
- `PUT` e `DELETE` exigem `If-Match` com o `ETag` da leitura: ausente → `428`; revisão que não é
  a atual → `412`, sem gravar nada; fora do formato (lista, ETag fraco, texto) → `400`. Sem `*`:
  ele aceitaria qualquer revisão, que é justamente o lost update que a revisão impede.
- Preset de outra conta ou inexistente → o mesmo `404`.

O `ETag` é a revisão entre aspas (`"3"`), documentado assim para a SPA derivá-lo do campo
`revision` do corpo sem depender do header (2.2). Versão `deprecated` leva `Sunset`.
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field

from goblinshift.catalog import CATALOG, RecipeVersionRetiredError
from goblinshift.entrypoints.api.catalog import (
    MAX_RECIPE_ID_CHARS,
    deprecation_headers,
    recipe_command_schema,
)
from goblinshift.entrypoints.api.dependencies import DbSession, require_scope
from goblinshift.entrypoints.api.json_command import JSON_MEDIA_TYPE, json_command
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode, problem_responses
from goblinshift.identity import Principal, Scope
from goblinshift.presets import (
    MAX_NAME_CHARS,
    MAX_PRESETS_PER_ACCOUNT,
    InvalidPresetError,
    Preset,
    PresetDraft,
    PresetLimitReachedError,
    PresetNameTakenError,
    PresetNotFoundError,
    StaleRevisionError,
    create_preset,
    delete_preset,
    get_preset,
    list_presets,
    parse_draft,
    replace_preset,
)

router = APIRouter(tags=["presets"])

PRESETS_PATH = "/presets"
PRESET_PATH = f"{PRESETS_PATH}/{{preset_id}}"
_IF_MATCH_HEADER = "If-Match"
# Um ETag forte com a revisão: `"12"`. Teto de dígitos só para não converter texto enorme.
_ETAG_PATTERN = re.compile(r'"([1-9][0-9]{0,8})"')
_ETAG_HEADER_DOC = {
    "ETag": {
        "description": 'Revisão entre aspas (`"3"`); envie-a em `If-Match` para alterar.',
        "required": True,
        "schema": {"type": "string"},
    }
}

ReadPresets = Annotated[Principal, Depends(require_scope(Scope.PRESETS_READ))]
WritePresets = Annotated[Principal, Depends(require_scope(Scope.PRESETS_WRITE))]


class PresetCommand(BaseModel):
    """Corpo de `POST` e `PUT`; os parâmetros são validados pelo catálogo, pela versão pedida."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    recipe_id: str = Field(min_length=1, max_length=MAX_RECIPE_ID_CHARS)
    recipe_version: int = Field(ge=1)
    parameters: dict[str, Any]


class PresetResponse(BaseModel):
    id: UUID
    name: str
    recipe_id: str
    recipe_version: int
    parameters: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime


class PresetListResponse(BaseModel):
    data: list[PresetResponse]


def _command_openapi(*, with_if_match: bool) -> dict[str, Any]:
    common = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_CHARS},
        },
        "required": ["name"],
    }
    extra: dict[str, Any] = {
        "requestBody": {
            "required": True,
            "content": {JSON_MEDIA_TYPE: {"schema": recipe_command_schema(common)}},
        }
    }
    if with_if_match:
        extra["parameters"] = [_if_match_parameter()]
    return extra


def _if_match_parameter() -> dict[str, Any]:
    return {
        "name": _IF_MATCH_HEADER,
        "in": "header",
        # Lido à mão: declarado na assinatura, a ausência viraria 422 e não o 428 do contrato.
        "required": True,
        "description": 'ETag da leitura (`"3"`); ausente → 428, de outra revisão → 412.',
        "schema": {"type": "string", "pattern": f"^{_ETAG_PATTERN.pattern}$"},
    }


@router.get(PRESETS_PATH, operation_id="listPresets")
def list_presets_route(principal: ReadPresets, session: DbSession) -> PresetListResponse:
    with session.begin():
        presets = list_presets(session, principal.owner_id)
    return PresetListResponse(data=[_preset_response(item) for item in presets])


@router.post(
    PRESETS_PATH,
    status_code=201,
    operation_id="createPreset",
    responses={
        201: {"description": "Preset criado.", "headers": _ETAG_HEADER_DOC},
        **problem_responses(400, 409, 413, 415),
    },
    openapi_extra=_command_openapi(with_if_match=False),
)
def create_preset_route(
    principal: WritePresets,
    command: Annotated[PresetCommand, Depends(json_command(PresetCommand))],
    session: DbSession,
    request: Request,
    response: Response,
) -> PresetResponse:
    draft = _parse(command)
    try:
        with session.begin():
            created = create_preset(session, principal.owner_id, draft)
    except PresetLimitReachedError:
        raise ApiProblem(
            ProblemCode.STATE_CONFLICT,
            f"A conta já tem {MAX_PRESETS_PER_ACCOUNT} presets; exclua um antes de criar outro.",
        ) from None
    except PresetNameTakenError:
        raise _name_taken() from None
    response.headers["Location"] = request.app.url_path_for(
        get_preset_route.__name__, preset_id=str(created.id)
    )
    return _respond(response, created)


@router.get(
    PRESET_PATH,
    operation_id="getPreset",
    responses={200: {"headers": _ETAG_HEADER_DOC}, **problem_responses(404)},
)
def get_preset_route(
    principal: ReadPresets, session: DbSession, response: Response, preset_id: UUID
) -> PresetResponse:
    try:
        with session.begin():
            found = get_preset(session, principal.owner_id, preset_id)
    except PresetNotFoundError:
        raise _not_found() from None
    return _respond(response, found)


@router.put(
    PRESET_PATH,
    operation_id="replacePreset",
    responses={
        200: {"headers": _ETAG_HEADER_DOC},
        **problem_responses(400, 404, 409, 412, 413, 415, 428),
    },
    openapi_extra=_command_openapi(with_if_match=True),
)
def replace_preset_route(
    principal: WritePresets,
    command: Annotated[PresetCommand, Depends(json_command(PresetCommand))],
    session: DbSession,
    request: Request,
    response: Response,
    preset_id: UUID,
) -> PresetResponse:
    expected = _expected_revision(request)
    draft = _parse(command)
    try:
        with session.begin():
            replaced = replace_preset(session, principal.owner_id, preset_id, expected, draft)
    except PresetNotFoundError:
        raise _not_found() from None
    except StaleRevisionError:
        raise _stale() from None
    except PresetNameTakenError:
        raise _name_taken() from None
    return _respond(response, replaced)


@router.delete(
    PRESET_PATH,
    status_code=204,
    operation_id="deletePreset",
    responses=problem_responses(400, 404, 412, 428),
    openapi_extra={"parameters": [_if_match_parameter()]},
)
def delete_preset_route(
    principal: WritePresets, session: DbSession, request: Request, preset_id: UUID
) -> Response:
    expected = _expected_revision(request)
    try:
        with session.begin():
            delete_preset(session, principal.owner_id, preset_id, expected)
    except PresetNotFoundError:
        raise _not_found() from None
    except StaleRevisionError:
        raise _stale() from None
    return Response(status_code=204)


def _parse(command: PresetCommand) -> PresetDraft:
    try:
        return parse_draft(
            command.name, command.recipe_id, command.recipe_version, command.parameters
        )
    except InvalidPresetError as error:
        raise RequestValidationError(
            [
                {
                    "loc": ("body", *field_error.location.split(".")),
                    "type": "value_error",
                    "msg": field_error.message,
                }
                for field_error in error.errors
            ]
        ) from None
    except RecipeVersionRetiredError:
        raise ApiProblem(
            ProblemCode.RECIPE_VERSION_RETIRED, "A versão da receita foi retirada."
        ) from None


def _expected_revision(request: Request) -> int:
    """Revisão do `If-Match`: um ETag forte desta API; outra forma não casa com a atual."""
    raw = request.headers.get(_IF_MATCH_HEADER, "").strip()
    if raw in {"", "*"}:
        raise ApiProblem(
            ProblemCode.PRECONDITION_REQUIRED,
            'Envie If-Match com o ETag lido do preset (por exemplo, "1").',
        )
    matched = _ETAG_PATTERN.fullmatch(raw)
    if matched is None:
        raise ApiProblem(
            ProblemCode.BAD_REQUEST,
            'If-Match deve ser um único ETag de preset, como "1".',
        )
    return int(matched[1])


def _respond(response: Response, preset: Preset) -> PresetResponse:
    response.headers["ETag"] = f'"{preset.revision}"'
    response.headers.update(
        deprecation_headers(CATALOG.get_version(preset.recipe_id, preset.recipe_version))
    )
    return _preset_response(preset)


def _preset_response(preset: Preset) -> PresetResponse:
    return PresetResponse(
        id=preset.id,
        name=preset.name,
        recipe_id=preset.recipe_id,
        recipe_version=preset.recipe_version,
        parameters=preset.parameters,
        revision=preset.revision,
        created_at=preset.created_at.astimezone(UTC),
        updated_at=preset.updated_at.astimezone(UTC),
    )


def _not_found() -> ApiProblem:
    return ApiProblem(ProblemCode.NOT_FOUND, "Preset não encontrado.")


def _stale() -> ApiProblem:
    return ApiProblem(
        ProblemCode.PRECONDITION_FAILED,
        "O preset mudou desde a leitura; leia de novo e use o ETag atual em If-Match.",
    )


def _name_taken() -> ApiProblem:
    return ApiProblem(ProblemCode.STATE_CONFLICT, "Já existe um preset com este nome na conta.")
