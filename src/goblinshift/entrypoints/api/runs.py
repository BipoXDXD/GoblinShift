"""Rotas de execuções: aceitar, acompanhar, listar e ver resultados (docs/GoblinShiftPlan.md, 1.6).

As rotas só traduzem: HTTP → comando do serviço `runs`, e erros do serviço → problem+json. Os
modelos de saída listam exatamente os campos publicados; dono, parâmetros brutos, limites,
geração e `traceparent` nunca saem daqui.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field
from sqlalchemy.orm import Session

from goblinshift.capacity import AccountCapacityExceededError, GlobalCapacityExceededError
from goblinshift.catalog import CATALOG, RecipeVersionRetiredError
from goblinshift.entrypoints.api.capacity_problems import (
    account_storage_problem,
    daily_quota_problem,
    environment_storage_problem,
)
from goblinshift.entrypoints.api.catalog import (
    MAX_RECIPE_ID_CHARS,
    RECIPE_ID_PATTERN,
    RecipeIdField,
    RecipeVersionField,
    deprecation_headers,
    recipe_command_schema,
)
from goblinshift.entrypoints.api.dependencies import DbSession, Now, require_scope
from goblinshift.entrypoints.api.idempotency import (
    REPLAYED_HEADER,
    idempotency_key_parameter,
    in_progress_problem,
    key_invalid_problem,
    parse_idempotency_header,
)
from goblinshift.entrypoints.api.json_command import (
    JSON_MEDIA_TYPE,
    body_validation_error,
    json_command,
)
from goblinshift.entrypoints.api.presets import (
    ETAG_PATTERN,
    if_match_parameter,
    parse_if_match_revision,
    stale_preset,
)
from goblinshift.entrypoints.api.problems import (
    ApiProblem,
    ProblemCode,
    problem_responses,
)
from goblinshift.entrypoints.api.wide_event import record_run_accepted
from goblinshift.files.errors import InputNotAvailableError
from goblinshift.files.limits import MAX_BATCH_FILES
from goblinshift.files.states import ArtifactRole, Availability
from goblinshift.identity import Principal, Scope
from goblinshift.platform.settings import Settings
from goblinshift.presets import PresetNotFoundError, get_preset
from goblinshift.processing.recipe import ItemNoticeCode, ItemRejectionCode, ItemWarningCode
from goblinshift.runs.acceptance import (
    AcceptanceError,
    Accepted,
    ActiveRunLimitError,
    DailyQuotaExceededError,
    IdempotencyInProgressError,
    IdempotencyKeyInvalidError,
    IdempotencyKeyReusedError,
    InvalidRunCommandError,
    QueueFullError,
    RunCommand,
    accept_run,
    find_replay,
    preset_run_hash,
    validate_run,
)
from goblinshift.runs.comparison import (
    AudioCodec,
    AudioFormat,
    ImageComparison,
    ImageFormat,
    ItemComparison,
    MediaComparison,
    PdfComparison,
    TableComparison,
)
from goblinshift.runs.cursor import CursorScopeMismatchError, InvalidCursorError
from goblinshift.runs.formula_cells import MAX_COLUMNS, MAX_REPORTED_COLUMN_NAME_CHARS, FormulaCells
from goblinshift.runs.lifecycle import ErrorCode, Phase, Status
from goblinshift.runs.queries import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ItemStatus,
    RunFilters,
    RunNotFoundError,
    RunView,
    get_results,
    get_run,
    get_run_detail,
    list_runs,
)

router = APIRouter(tags=["runs"])


def _reject_unix_timestamp(value: object) -> object:
    """O Pydantic em modo lax lê `"0.5"` como timestamp Unix; a spec publica RFC 3339."""
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            return value
        raise ValueError("Use um instante RFC 3339 com fuso, como 2026-10-01T12:00:00Z.")
    return value


PeriodBound = Annotated[AwareDatetime, BeforeValidator(_reject_unix_timestamp)]

RUNS_PATH = "/runs"
# Intervalo de polling esperado enquanto a execução não termina (1.6, "Acompanhamento").
POLL_RETRY_AFTER_SECONDS = 2
# Uma execução ocupa o executor por minutos (1.7); tentar antes disso só gasta a cota.
QUEUE_FULL_RETRY_AFTER_SECONDS = 60
MAX_CURSOR_CHARS = 1024
# Explícito, não o global: a API esvazia o propagador global para não aceitar o contexto de trace
# do cliente (configure_tracing), mas a Run grava o `traceparent` da aceitação para o worker.
_PROPAGATOR = TraceContextTextMapPropagator()
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
    ErrorCode.INPUT_TAMPERED: "Um arquivo de entrada não passou na verificação de integridade.",
    ErrorCode.CANCELED: "A execução foi cancelada.",
}
_GENERIC_ERROR_MESSAGE = "A execução falhou."


class NewRun(BaseModel):
    """Corpo de `POST /runs`; os parâmetros são validados pelo catálogo, pela versão pedida."""

    model_config = ConfigDict(extra="forbid")

    recipe_id: RecipeIdField
    recipe_version: RecipeVersionField
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
    # Parâmetros resolvidos (com defaults), para repetir a configuração; nulos depois de 30 dias.
    parameters: dict[str, Any] | None
    # Preset de origem, se houve; nulo também quando o preset foi excluído.
    preset_id: UUID | None
    # Revisão do preset cujos parâmetros a Run usou; nula sem preset e nas Runs antigas. Fica
    # mesmo depois de excluir o preset.
    preset_revision: int | None
    # Vencimento do primeiro resultado ainda disponível; nulo se não há resultado disponível
    # (antes da publicação, sem saídas, excluídos ou vencidos).
    results_expire_at: datetime | None


class RunSummaryResponse(BaseModel):
    inputs: int
    ok: int
    rejected: int
    duplicates: int
    outputs: int


class FormulaColumnResponse(BaseModel):
    # Nome da coluna de saída como texto puro: é dado do usuário, cortado com "…" no teto. Nulo
    # quando o cabeçalho é vazio ou só de espaços (a tela dá o rótulo).
    name: str | None = Field(min_length=1, max_length=MAX_REPORTED_COLUMN_NAME_CHARS)
    cells: int = Field(ge=1)


class FormulaCellsResponse(BaseModel):
    """Contagem por coluna de `CSV_FORMULA_CELLS`: as maiores primeiro, até o teto (1.3)."""

    columns: list[FormulaColumnResponse] = Field(min_length=1, max_length=MAX_COLUMNS)
    # Colunas com células que parecem fórmula e que ficaram fora de `columns` pelo teto.
    more_columns: int = Field(ge=0)


class TableComparisonResponse(BaseModel):
    """CSV Janitor: registros lidos → escritos."""

    kind: Literal["table"]
    records_before: int = Field(ge=0)
    records_after: int = Field(ge=0)


class PdfComparisonResponse(BaseModel):
    """PDF Press: páginas da entrada → da saída."""

    kind: Literal["pdf"]
    pages_before: int = Field(ge=0)
    pages_after: int = Field(ge=0)


class ImageComparisonResponse(BaseModel):
    """Image Forge: dimensões, formato e tamanho em bytes."""

    kind: Literal["image"]
    width_before: int = Field(ge=1)
    height_before: int = Field(ge=1)
    width_after: int = Field(ge=1)
    height_after: int = Field(ge=1)
    format_before: ImageFormat
    format_after: ImageFormat
    bytes_before: int = Field(ge=0)
    # Tamanho da saída medido pelo worker na coleta (ADR 059); nulo se não houve medida.
    bytes_after: int | None = Field(ge=0)


class MediaComparisonResponse(BaseModel):
    """Media Mill: vídeo → miniatura e áudio de origem → extraído; cada par vem inteiro ou nulo."""

    kind: Literal["media"]
    width_before: int = Field(ge=1)
    height_before: int = Field(ge=1)
    width_after: int | None = Field(ge=1)
    height_after: int | None = Field(ge=1)
    audio_before: AudioCodec | None
    audio_after: AudioFormat | None


ItemComparisonResponse = Annotated[
    TableComparisonResponse
    | PdfComparisonResponse
    | ImageComparisonResponse
    | MediaComparisonResponse,
    Field(discriminator="kind"),
]


class RunItemResponse(BaseModel):
    """Desfecho de uma entrada: códigos do catálogo e contagens, sem valor do conteúdo."""

    position: int
    upload_id: UUID
    # Nome do próprio upload, para a tela não ler upload a upload; nulo depois de 30 dias.
    original_name: str | None
    status: ItemStatus
    error_code: ItemRejectionCode | None
    warnings: list[ItemWarningCode]
    # Fatos que não são aviso e não mudam o estado (A2), como a conversão de Display P3.
    notices: list[ItemNoticeCode]
    counts: dict[str, int]
    duplicate_of: int | None
    # Só com o aviso CSV_FORMULA_CELLS; nulo sem ele e depois de 30 dias, como `counts`.
    formula_cells: FormulaCellsResponse | None
    # Antes → depois que a receita já mede (2.2); nulo sem dado e depois de 30 dias, como `counts`.
    comparison: ItemComparisonResponse | None


class RunDetailResponse(RunResponse):
    summary: RunSummaryResponse | None
    items: list[RunItemResponse]


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


_ANONYMIZED_NAME_NOTE = (
    " Trinta dias depois do término da execução o nome é anulado (retenção de 1.7) e vem como "
    "`<role>-<artifact_id>`."
)


class ResultReferenceResponse(BaseModel):
    artifact_id: UUID
    logical_name: str = Field(
        description="Nome da saída dentro da execução." + _ANONYMIZED_NAME_NOTE
    )
    role: ArtifactRole
    file_name: str = Field(
        description="Nome com que o download entrega o arquivo." + _ANONYMIZED_NAME_NOTE
    )
    availability: Availability
    # Só no papel `preview`: a saída que ele mostra, para a interface pôr os dois juntos (2.2).
    source_artifact_id: UUID | None


class RunResultsResponse(BaseModel):
    published: bool
    expires_at: datetime | None
    artifacts: list[ResultReferenceResponse]


def _problem(*statuses: int) -> dict[int | str, dict[str, Any]]:
    return problem_responses(*statuses)


def _create_run_openapi() -> dict[str, Any]:
    """Corpo publicado como as variantes do catálogo, mais a lista de entradas (1.6)."""
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
    schema = recipe_command_schema(common)
    idempotency_key = idempotency_key_parameter(
        required=False, description="Obrigatória, exceto com validate_only."
    )
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
    # Lida à mão e publicada em `_create_run_openapi` (motivo em `idempotency`).
    idempotency_key = parse_idempotency_header(request)
    if validate_only:
        response.status_code = 200
        validated = _validate(run_command)
        _add_deprecation_headers(response, validated.recipe_id, validated.recipe_version)
        return validated
    return _accept(principal.owner_id, run_command, idempotency_key, session, request, response)


class NewPresetRun(BaseModel):
    """Corpo de `POST /presets/{preset_id}/runs`: só as entradas; a configuração vem do preset."""

    model_config = ConfigDict(extra="forbid")

    input_upload_ids: list[UUID] = Field(max_length=MAX_BATCH_FILES)


def _preset_run_openapi() -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "input_upload_ids": {
                "description": "Uploads prontos, na ordem de processamento, sem repetição "
                "(1 a max_inputs da versão do preset).",
                "type": "array",
                "items": {"type": "string", "format": "uuid"},
                "uniqueItems": True,
                "minItems": 1,
                "maxItems": MAX_BATCH_FILES,
            },
        },
        "required": ["input_upload_ids"],
        "additionalProperties": False,
    }
    idempotency_key = idempotency_key_parameter(required=True)
    if_match = {
        **if_match_parameter(),
        # Opcional aqui: ausente ou `*` roda a revisão atual do preset.
        "required": False,
        "description": 'ETag da leitura do preset (`"3"`); de outra revisão → 412. Ausente ou '
        "`*`: roda a revisão atual.",
        "schema": {"type": "string", "pattern": rf"^(\*|{ETAG_PATTERN.pattern})$"},
    }
    return {
        "parameters": [idempotency_key, if_match],
        "requestBody": {"required": True, "content": {JSON_MEDIA_TYPE: {"schema": schema}}},
    }


@router.post(
    "/presets/{preset_id}/runs",
    status_code=202,
    operation_id="createRunFromPreset",
    responses=_problem(400, 404, 409, 412, 413, 415, 429, 503),
    openapi_extra=_preset_run_openapi(),
)
def create_run_from_preset(
    principal: Annotated[Principal, Depends(require_scope(Scope.RUNS_WRITE))],
    _reader: Annotated[Principal, Depends(require_scope(Scope.PRESETS_READ))],
    command: Annotated[NewPresetRun, Depends(json_command(NewPresetRun))],
    session: DbSession,
    request: Request,
    response: Response,
    preset_id: UUID,
) -> AcceptedRunResponse:
    """Nova execução com a receita, a versão e os parâmetros guardados no preset (1.1).

    Exige também `presets:read`, porque a configuração do preset passa a aparecer na execução.
    A Run guarda o `preset_id` e a revisão usada; editar ou excluir o preset depois não a altera.
    `If-Match` opcional: com o ETag de outra revisão, 412 sem criar a Run.
    """
    idempotency_key = parse_idempotency_header(request)
    expected_revision = parse_if_match_revision(request, required=False)
    inputs = tuple(command.input_upload_ids)
    try:
        # O replay vem antes de ler o preset: editado ou excluído depois da primeira chamada, o
        # retry repete a Run aceita em vez de virar 422 ou 404.
        with session.begin():
            replay = find_replay(
                session, principal.owner_id, idempotency_key, preset_run_hash(preset_id, inputs)
            )
            if replay is None:
                source = get_preset(session, principal.owner_id, preset_id)
    except _REFUSALS as error:
        raise _refusal_problem(error) from None
    except PresetNotFoundError:
        raise _preset_not_found() from None
    if replay is not None:
        return _respond_accepted(principal.owner_id, replay, session, request, response)
    if expected_revision is not None and source.revision != expected_revision:
        raise stale_preset()
    run_command = RunCommand(
        recipe_id=source.recipe_id,
        recipe_version=source.recipe_version,
        input_upload_ids=inputs,
        parameters=source.parameters,
        preset_id=source.id,
        preset_revision=source.revision,
    )
    return _accept(principal.owner_id, run_command, idempotency_key, session, request, response)


def _accept(
    owner_id: UUID,
    run_command: RunCommand,
    idempotency_key: str | None,
    session: Session,
    request: Request,
    response: Response,
) -> AcceptedRunResponse:
    """Aceita e monta o `202`: `Location`, replay e `Sunset` da versão usada."""
    try:
        accepted = accept_run(
            session,
            owner_id,
            run_command,
            idempotency_key,
            _current_traceparent(),
            executor_image=request.app.state.executor_image,
            quota_defaults=request.app.state.quota_defaults,
        )
    except _REFUSALS as error:
        raise _refusal_problem(error) from None
    except PresetNotFoundError:
        # Excluído entre a leitura e a aceitação: o mesmo 404 de quem não o encontrou.
        raise _preset_not_found() from None
    except AccountCapacityExceededError:
        raise account_storage_problem(session, owner_id) from None
    except GlobalCapacityExceededError:
        raise environment_storage_problem() from None
    return _respond_accepted(owner_id, accepted, session, request, response)


def _respond_accepted(
    owner_id: UUID, accepted: Accepted, session: Session, request: Request, response: Response
) -> AcceptedRunResponse:
    view = _read_run(session, owner_id, accepted.run_id)
    record_run_accepted(
        accepted.run_id, view.recipe_id, view.recipe_version, replayed=accepted.replayed
    )
    response.headers["Location"] = request.app.url_path_for(
        get_run_route.__name__, run_id=str(accepted.run_id)
    )
    if accepted.replayed:
        response.headers[REPLAYED_HEADER] = "true"
    _add_deprecation_headers(response, view.recipe_id, view.recipe_version)
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
        str | None, Query(max_length=MAX_RECIPE_ID_CHARS, pattern=RECIPE_ID_PATTERN)
    ] = None,
    status: Status | None = None,
    created_from: PeriodBound | None = None,
    created_to: PeriodBound | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_CHARS)] = None,
) -> RunListResponse:
    settings: Settings = request.app.state.settings
    filters = RunFilters(
        recipe_id,
        status,
        _in_utc("created_from", created_from),
        _in_utc("created_to", created_to),
    )
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
) -> RunDetailResponse:
    try:
        with session.begin():
            detail = get_run_detail(session, principal.owner_id, run_id)
    except RunNotFoundError:
        raise _run_not_found() from None
    if not detail.view.is_terminal:
        response.headers["Retry-After"] = str(POLL_RETRY_AFTER_SECONDS)
    summary = detail.summary
    return RunDetailResponse(
        **_run_response(detail.view).model_dump(),
        summary=RunSummaryResponse(**summary) if summary is not None else None,
        items=[
            RunItemResponse(
                position=item.position,
                upload_id=item.upload_id,
                original_name=item.original_name,
                status=item.status,
                error_code=item.error_code,
                warnings=list(item.warnings),
                notices=list(item.notices),
                counts=item.counts,
                duplicate_of=item.duplicate_of,
                formula_cells=_formula_cells(item.formula_cells),
                comparison=_comparison(item.comparison),
            )
            for item in detail.items
        ],
    )


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
                availability=ref.availability,
                source_artifact_id=ref.source_artifact_id,
            )
            for ref in results.references
        ],
    )


def _add_deprecation_headers(response: Response, recipe_id: str, version: int) -> None:
    """`Sunset` quando a versão usada está `deprecated` (1.6); a aceitação já a encontrou."""
    response.headers.update(deprecation_headers(CATALOG.get_version(recipe_id, version)))


def _in_utc(name: str, moment: datetime | None) -> datetime | None:
    """Filtro de período em UTC; um instante válido com fuso pode cair fora da faixa do ano 1 a
    9999 quando convertido, e isso é entrada inválida (422), não erro do servidor."""
    if moment is None:
        return None
    try:
        return moment.astimezone(UTC)
    except OverflowError:
        raise RequestValidationError(
            [
                {
                    "loc": ("query", name),
                    "type": "value_error",
                    "msg": "a data fica fora da faixa válida em UTC",
                }
            ]
        ) from None


def _validate(command: RunCommand) -> ValidatedRunResponse:
    """Sem chave é permitido; enviada, `parse_idempotency_header` já conferiu o formato."""
    try:
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
            return key_invalid_problem()
        case InvalidRunCommandError():
            return body_validation_error(error.errors)
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
            return in_progress_problem()
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


def _preset_not_found() -> ApiProblem:
    return ApiProblem(ProblemCode.NOT_FOUND, "Preset não encontrado.")


def _formula_cells(summary: FormulaCells | None) -> FormulaCellsResponse | None:
    if summary is None:
        return None
    return FormulaCellsResponse(
        columns=[FormulaColumnResponse(name=c.name, cells=c.cells) for c in summary.columns],
        more_columns=summary.more_columns,
    )


def _comparison(comparison: ItemComparison | None) -> ItemComparisonResponse | None:
    match comparison:
        case None:
            return None
        case TableComparison():
            return TableComparisonResponse(
                kind="table",
                records_before=comparison.records_before,
                records_after=comparison.records_after,
            )
        case PdfComparison():
            return PdfComparisonResponse(
                kind="pdf", pages_before=comparison.pages_before, pages_after=comparison.pages_after
            )
        case ImageComparison():
            return ImageComparisonResponse(
                kind="image",
                width_before=comparison.width_before,
                height_before=comparison.height_before,
                width_after=comparison.width_after,
                height_after=comparison.height_after,
                format_before=comparison.format_before,
                format_after=comparison.format_after,
                bytes_before=comparison.bytes_before,
                bytes_after=comparison.bytes_after,
            )
        case MediaComparison():
            return MediaComparisonResponse(
                kind="media",
                width_before=comparison.width_before,
                height_before=comparison.height_before,
                width_after=comparison.width_after,
                height_after=comparison.height_after,
                audio_before=comparison.audio_before,
                audio_after=comparison.audio_after,
            )


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
        parameters=view.parameters,
        preset_id=view.preset_id,
        preset_revision=view.preset_revision,
        results_expire_at=(
            _utc(view.results_expire_at) if view.results_expire_at is not None else None
        ),
    )


def _current_traceparent() -> str | None:
    """`traceparent` do span da requisição, para ligar o trabalho do worker à aceitação (3.7)."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier.get("traceparent")


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)
