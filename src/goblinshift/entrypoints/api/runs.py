"""Rotas de execuções: aceitar, acompanhar, listar e ver resultados (docs/GoblinShiftPlan.md, 1.6).

As rotas só traduzem: HTTP → comando do serviço `runs`, e erros do serviço → problem+json. Os
modelos de saída listam exatamente os campos publicados; dono, parâmetros brutos, limites,
geração e `traceparent` nunca saem daqui.
"""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from opentelemetry import propagate
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from goblinshift.capacity import AccountCapacityExceededError, GlobalCapacityExceededError
from goblinshift.catalog import CATALOG, RecipeVersionRetiredError, VersionStatus
from goblinshift.entrypoints.api.capacity_problems import (
    account_storage_problem,
    daily_quota_problem,
    environment_storage_problem,
)
from goblinshift.entrypoints.api.dependencies import DbSession, Now, require_scope
from goblinshift.entrypoints.api.json_command import JSON_MEDIA_TYPE, json_command
from goblinshift.entrypoints.api.problems import (
    ApiProblem,
    ProblemCode,
    problem_responses,
)
from goblinshift.entrypoints.api.wide_event import record_run_accepted
from goblinshift.files.errors import InputNotAvailableError
from goblinshift.files.limits import MAX_BATCH_FILES
from goblinshift.files.states import ArtifactRole
from goblinshift.identity import Principal, Scope
from goblinshift.platform.settings import Settings
from goblinshift.runs.acceptance import (
    IDEMPOTENCY_KEY_PATTERN,
    AcceptanceError,
    ActiveRunLimitError,
    DailyQuotaExceededError,
    IdempotencyInProgressError,
    IdempotencyKeyInvalidError,
    IdempotencyKeyReusedError,
    InvalidRunCommandError,
    QueueFullError,
    RunCommand,
    accept_run,
    parse_idempotency_key,
    validate_run,
)
from goblinshift.runs.cursor import CursorScopeMismatchError, InvalidCursorError
from goblinshift.runs.lifecycle import ErrorCode, Phase, Status
from goblinshift.runs.queries import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    RunFilters,
    RunNotFoundError,
    RunView,
    get_results,
    get_run,
    list_runs,
)

router = APIRouter(tags=["runs"])

RUNS_PATH = "/runs"
# Intervalo de polling esperado enquanto a execução não termina (1.6, "Acompanhamento").
POLL_RETRY_AFTER_SECONDS = 2
# A requisição original costuma terminar em milissegundos; um segundo basta para o replay.
IN_PROGRESS_RETRY_AFTER_SECONDS = 1
# Uma execução ocupa o executor por minutos (1.7); tentar antes disso só gasta a cota.
QUEUE_FULL_RETRY_AFTER_SECONDS = 60
MAX_RECIPE_ID_CHARS = 64
# Formato dos ids do catálogo (`csv-janitor`): o filtro da listagem só aceita ids nesse formato.
_RECIPE_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
MAX_CURSOR_CHARS = 1024
_IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
_REPLAYED_HEADER = "Idempotency-Replayed"
_ERROR_MESSAGES: dict[str, str] = {
    ErrorCode.NO_VALID_OUTPUTS: "Nenhuma entrada produziu saída válida.",
    ErrorCode.OUTPUT_LIMIT_EXCEEDED: "As saídas passaram do limite da execução.",
    ErrorCode.REPORT_LIMIT_EXCEEDED: "O relatório passou do limite de tamanho.",
    ErrorCode.RESOURCE_LIMIT_EXCEEDED: "A execução passou dos limites de memória, disco ou tempo.",
    ErrorCode.INPUT_LIMIT_EXCEEDED: "As entradas passaram de um limite da receita.",
    ErrorCode.PDF_INVALID: "Um dos PDFs está danificado ou não é um PDF.",
    ErrorCode.PDF_UNSUPPORTED_FEATURE: (
        "Um dos PDFs tem senha, formulário, assinatura, anexo ou conteúdo ativo."
    ),
    ErrorCode.PDF_PAGE_NOT_FOUND: "Uma das páginas escolhidas não existe no documento.",
    ErrorCode.EXECUTOR_UNAVAILABLE: "O executor ficou indisponível nas tentativas permitidas.",
    ErrorCode.QUEUE_EXPIRED: "A execução não começou dentro do prazo da fila.",
    ErrorCode.DEADLINE_EXCEEDED: "A execução passou do prazo total.",
    ErrorCode.INFRA_POISON: "A execução foi interrompida por falhas repetidas de infraestrutura.",
    ErrorCode.CANCELED: "A execução foi cancelada.",
}
_GENERIC_ERROR_MESSAGE = "A execução falhou."


class NewRun(BaseModel):
    """Corpo de `POST /runs`; os parâmetros são validados pelo catálogo, pela versão pedida."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: str = Field(min_length=1, max_length=MAX_RECIPE_ID_CHARS)
    recipe_version: int = Field(ge=1)
    input_upload_ids: list[UUID] = Field(default_factory=list, max_length=MAX_BATCH_FILES)
    parameters: dict[str, Any]


class Progress(BaseModel):
    phase: Phase | None
    processed: int
    total: int


class RunErrorBody(BaseModel):
    code: str
    message: str


class RunResponse(BaseModel):
    id: UUID
    recipe_id: str
    recipe_version: int
    status: Status
    attempt: int
    progress: Progress
    queue_ahead: int
    created_at: datetime
    queue_expires_at: datetime
    deadline_at: datetime | None
    error: RunErrorBody | None


class AcceptedRunResponse(RunResponse):
    idempotency_expires_at: datetime


class ValidatedRunResponse(BaseModel):
    recipe_id: str
    recipe_version: int
    input_upload_ids: list[UUID]
    parameters: dict[str, Any]


class RunListResponse(BaseModel):
    data: list[RunResponse]
    next_cursor: str | None


class ResultReferenceResponse(BaseModel):
    artifact_id: UUID
    logical_name: str
    role: ArtifactRole
    file_name: str


class RunResultsResponse(BaseModel):
    published: bool
    expires_at: datetime | None
    artifacts: list[ResultReferenceResponse]


def _problem(*statuses: int) -> dict[int | str, dict[str, Any]]:
    return problem_responses(*statuses)


def _create_run_openapi() -> dict[str, Any]:
    """Corpo publicado como `allOf` (campos comuns) + `oneOf` das versões aceitas (1.6).

    `unevaluatedProperties: false` fica no nível do `allOf`, o único em que o JSON Schema
    2020-12 enxerga as propriedades avaliadas pelas duas partes (ver `catalog.versions`).
    """
    variants = [
        version.variant_schema()
        for version in CATALOG.list_versions()
        if version.status is not VersionStatus.RETIRED
    ]
    common = {
        "type": "object",
        "properties": {
            "input_upload_ids": {
                "description": "Uploads prontos, na ordem de processamento, sem repetição. "
                "Obrigatório (1 a max_inputs da versão), exceto com validate_only.",
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
                "uniqueItems": True,
                "maxItems": MAX_BATCH_FILES,
            },
        },
    }
    schema = {"allOf": [common, {"oneOf": variants}], "unevaluatedProperties": False}
    idempotency_key = {
        "name": _IDEMPOTENCY_KEY_HEADER,
        "in": "header",
        "required": False,
        "description": "Obrigatória, exceto com validate_only.",
        "schema": {"type": "string", "pattern": f"^{IDEMPOTENCY_KEY_PATTERN.pattern}$"},
    }
    return {
        "parameters": [idempotency_key],
        "requestBody": {"required": True, "content": {JSON_MEDIA_TYPE: {"schema": schema}}},
    }


@router.post(
    RUNS_PATH,
    status_code=202,
    operation_id="createRun",
    response_model=AcceptedRunResponse | ValidatedRunResponse,
    responses={
        200: {"model": ValidatedRunResponse, "description": "validate_only: parâmetros resolvidos"},
        **_problem(400, 409, 413, 415, 429, 503),
    },
    openapi_extra=_create_run_openapi(),
)
def create_run(
    principal: Annotated[Principal, Depends(require_scope(Scope.RUNS_WRITE))],
    command: Annotated[NewRun, Depends(json_command(NewRun))],
    session: DbSession,
    request: Request,
    response: Response,
    validate_only: bool = False,
) -> AcceptedRunResponse | ValidatedRunResponse:
    run_command = RunCommand(
        recipe_id=command.recipe_id,
        recipe_version=command.recipe_version,
        input_upload_ids=tuple(command.input_upload_ids),
        parameters=command.parameters,
    )
    # Lida à mão e publicada em `_create_run_openapi`: declarada como `str | None` na assinatura,
    # sairia na spec como `anyOf [string, null]`, e o cliente gerado mandaria o texto "null".
    idempotency_key = request.headers.get(_IDEMPOTENCY_KEY_HEADER)
    if validate_only:
        response.status_code = 200
        return _validate(run_command, idempotency_key)
    try:
        accepted = accept_run(
            session,
            principal.owner_id,
            run_command,
            idempotency_key,
            _current_traceparent(),
            executor_image=request.app.state.executor_image,
            quota_defaults=request.app.state.quota_defaults,
        )
    except _REFUSALS as error:
        raise _refusal_problem(error) from None
    except AccountCapacityExceededError:
        raise account_storage_problem(session, principal.owner_id) from None
    except GlobalCapacityExceededError:
        raise environment_storage_problem() from None
    record_run_accepted(
        accepted.run_id,
        run_command.recipe_id,
        run_command.recipe_version,
        replayed=accepted.replayed,
    )
    view = _read_run(session, principal.owner_id, accepted.run_id)
    response.headers["Location"] = request.app.url_path_for(
        get_run_route.__name__, run_id=str(accepted.run_id)
    )
    if accepted.replayed:
        response.headers[_REPLAYED_HEADER] = "true"
    return AcceptedRunResponse(
        **_run_response(view).model_dump(),
        idempotency_expires_at=_utc(accepted.idempotency_expires_at),
    )


@router.get(RUNS_PATH, operation_id="listRuns", responses=_problem(400))
def list_runs_route(
    principal: Annotated[Principal, Depends(require_scope(Scope.RUNS_READ))],
    session: DbSession,
    request: Request,
    now: Now,
    recipe_id: Annotated[
        str | None, Query(max_length=MAX_RECIPE_ID_CHARS, pattern=_RECIPE_ID_PATTERN)
    ] = None,
    status: Status | None = None,
    created_from: AwareDatetime | None = None,
    created_to: AwareDatetime | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_CHARS)] = None,
) -> RunListResponse:
    settings: Settings = request.app.state.settings
    filters = RunFilters(recipe_id, status, created_from, created_to)
    try:
        with session.begin():
            page = list_runs(
                session,
                principal.owner_id,
                filters,
                limit=limit,
                cursor=cursor,
                secret=settings.cursor_signing_key(),
                now=now,
            )
    except CursorScopeMismatchError:
        raise ApiProblem(
            ProblemCode.BAD_REQUEST, "O cursor foi emitido para outros filtros."
        ) from None
    except InvalidCursorError:
        raise ApiProblem(ProblemCode.BAD_REQUEST, "O cursor é inválido ou venceu.") from None
    return RunListResponse(
        data=[_run_response(view) for view in page.items], next_cursor=page.next_cursor
    )


@router.get(f"{RUNS_PATH}/{{run_id}}", operation_id="getRun", responses=_problem(404))
def get_run_route(
    principal: Annotated[Principal, Depends(require_scope(Scope.RUNS_READ))],
    session: DbSession,
    response: Response,
    run_id: UUID,
) -> RunResponse:
    view = _read_run(session, principal.owner_id, run_id)
    if not view.is_terminal:
        response.headers["Retry-After"] = str(POLL_RETRY_AFTER_SECONDS)
    return _run_response(view)


@router.get(
    f"{RUNS_PATH}/{{run_id}}/results", operation_id="getRunResults", responses=_problem(404)
)
def get_results_route(
    principal: Annotated[Principal, Depends(require_scope(Scope.RUNS_READ))],
    session: DbSession,
    run_id: UUID,
) -> RunResultsResponse:
    try:
        with session.begin():
            results = get_results(session, principal.owner_id, run_id)
    except RunNotFoundError:
        raise _run_not_found() from None
    return RunResultsResponse(
        published=results.published,
        expires_at=_utc(results.expires_at) if results.expires_at is not None else None,
        artifacts=[
            ResultReferenceResponse(
                artifact_id=ref.artifact_id,
                logical_name=ref.logical_name,
                role=ref.role,
                file_name=ref.file_name,
            )
            for ref in results.references
        ],
    )


def _validate(command: RunCommand, idempotency_key: str | None) -> ValidatedRunResponse:
    """Sem chave é permitido; enviada, a chave tem de estar no formato publicado."""
    try:
        if idempotency_key is not None:
            parse_idempotency_key(idempotency_key)
        validated = validate_run(command)
    except _REFUSALS as error:
        raise _refusal_problem(error) from None
    return ValidatedRunResponse(
        recipe_id=validated.recipe.recipe_id,
        recipe_version=validated.recipe.version,
        input_upload_ids=list(validated.input_upload_ids),
        parameters=validated.parameters,
    )


_REFUSALS = (AcceptanceError, RecipeVersionRetiredError, InputNotAvailableError)


def _refusal_problem(error: Exception) -> ApiProblem | RequestValidationError:
    """Recusa do serviço → problem+json; cada recusa tem um código só (1.6)."""
    match error:
        case IdempotencyKeyInvalidError():
            return ApiProblem(
                ProblemCode.IDEMPOTENCY_KEY_INVALID,
                "Envie Idempotency-Key com 16 a 64 caracteres [A-Za-z0-9_-].",
            )
        case InvalidRunCommandError():
            return RequestValidationError(
                [
                    {
                        "loc": ("body", *field_error.location.split(".")),
                        "type": "value_error",
                        "msg": field_error.message,
                    }
                    for field_error in error.errors
                ]
            )
        case RecipeVersionRetiredError():
            return ApiProblem(
                ProblemCode.RECIPE_VERSION_RETIRED, "A versão da receita foi retirada."
            )
        case IdempotencyKeyReusedError():
            return ApiProblem(
                ProblemCode.IDEMPOTENCY_KEY_REUSED,
                "A Idempotency-Key já foi usada com outro comando.",
            )
        case IdempotencyInProgressError():
            return ApiProblem(
                ProblemCode.IDEMPOTENCY_IN_PROGRESS,
                "A requisição original com esta Idempotency-Key ainda está em andamento.",
                headers={"Retry-After": str(IN_PROGRESS_RETRY_AFTER_SECONDS)},
            )
        case QueueFullError():
            return ApiProblem(
                ProblemCode.QUEUE_FULL,
                "A fila global está cheia; tente de novo mais tarde.",
                headers={"Retry-After": str(QUEUE_FULL_RETRY_AFTER_SECONDS)},
            )
        case DailyQuotaExceededError():
            return daily_quota_problem(error.retry_after)
        case ActiveRunLimitError():
            return ApiProblem(
                ProblemCode.ACTIVE_RUN_LIMIT, "A conta já tem uma execução não terminal."
            )
        case InputNotAvailableError():
            # Um texto só para todos os motivos, sem citar IDs: não permite enumeração (1.6).
            return ApiProblem(
                ProblemCode.INPUT_NOT_AVAILABLE, "Alguma entrada citada não está disponível."
            )
        case _:
            raise TypeError(f"recusa sem tradução HTTP: {type(error).__name__}") from error


def _read_run(session: Session, owner_id: UUID, run_id: UUID) -> RunView:
    try:
        with session.begin():
            return get_run(session, owner_id, run_id)
    except RunNotFoundError:
        raise _run_not_found() from None


def _run_not_found() -> ApiProblem:
    return ApiProblem(ProblemCode.NOT_FOUND, "Execução não encontrada.")


def _run_response(view: RunView) -> RunResponse:
    error = None
    if view.error_code is not None:
        message = _ERROR_MESSAGES.get(view.error_code, _GENERIC_ERROR_MESSAGE)
        error = RunErrorBody(code=view.error_code, message=message)
    return RunResponse(
        id=view.id,
        recipe_id=view.recipe_id,
        recipe_version=view.recipe_version,
        status=view.status,
        attempt=view.attempt,
        progress=Progress(phase=view.phase, processed=view.processed, total=view.total),
        queue_ahead=view.queue_ahead,
        created_at=_utc(view.created_at),
        queue_expires_at=_utc(view.queue_expires_at),
        deadline_at=_utc(view.deadline_at) if view.deadline_at is not None else None,
        error=error,
    )


def _current_traceparent() -> str | None:
    """`traceparent` do span da requisição, para ligar o trabalho do worker à aceitação (3.7)."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)
