"""Rotas de upload (docs/GoblinShiftPlan.md, 1.6, 1.7 e 3.6).

`POST /uploads` cria um lote de intenções; cada uma traz `content: {method, url, headers}`, que o
cliente usa exatamente como recebido. `PUT /uploads/{id}/content` recebe os bytes por streaming,
ocupando uma vaga de transferência, sem transação aberta durante a transferência.

Inexistente e de outra conta dão o mesmo 404; expirado ou excluído do próprio dono, 410.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from goblinshift.capacity import AccountCapacityExceededError, GlobalCapacityExceededError
from goblinshift.entrypoints.api.capacity_problems import (
    account_storage_problem,
    environment_storage_problem,
)
from goblinshift.entrypoints.api.dependencies import DbSession, ReadFiles, Storage, WriteFiles
from goblinshift.entrypoints.api.json_command import command_openapi, json_command
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode, problem_responses
from goblinshift.entrypoints.api.transfers import (
    OCTET_STREAM,
    OCTET_STREAM_BODY,
    TransferTimeoutError,
    is_octet_stream,
    request_body_chunks,
    timeouts_of,
    transfer_limit_problem,
)
from goblinshift.files.errors import (
    FilesError,
    InvalidUploadRequestError,
    TransferLimitReachedError,
    UploadContentTooLargeError,
    UploadGoneError,
    UploadNotFoundError,
    UploadSizeMismatchError,
    UploadStateConflictError,
)
from goblinshift.files.limits import (
    MAX_BATCH_BYTES,
    MAX_BATCH_FILES,
    MAX_FILE_BYTES,
    MAX_NAME_CHARS,
    MIB,
)
from goblinshift.files.states import Availability, TransferDirection, UploadState
from goblinshift.files.transfers import acquire_transfer_slot, release_transfer_slot
from goblinshift.files.uploads import (
    UPLOAD_NAME_PATTERN,
    UploadView,
    create_intents,
    get_upload,
    receive_content,
    request_deletion,
)

router = APIRouter(tags=["uploads"])

# Nomes do serviço que mudam na borda HTTP.
_API_FIELD_NAMES = {"expected_size": "size"}


class UploadItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # O padrão só é publicado: quem o aplica é `files.uploads`, que distingue separador de controle
    # na mensagem de erro e também recusa "." e "..".
    name: str = Field(
        min_length=1, max_length=MAX_NAME_CHARS, json_schema_extra={"pattern": UPLOAD_NAME_PATTERN}
    )
    size: int = Field(
        ge=0, le=MAX_FILE_BYTES, description=f"Bytes; o lote soma até {MAX_BATCH_BYTES // MIB} MiB."
    )


class CreateUploads(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[UploadItem] = Field(min_length=1, max_length=MAX_BATCH_FILES)


class ContentInstructions(BaseModel):
    method: Literal["PUT"]
    url: str
    headers: dict[str, str]


class CreatedUpload(BaseModel):
    id: UUID
    name: str
    size: int
    state: UploadState
    expires_at: datetime
    content: ContentInstructions


class CreatedUploads(BaseModel):
    items: list[CreatedUpload]


class ReceivedContent(BaseModel):
    id: UUID
    state: UploadState
    size: int
    sha256: str


class UploadDetails(BaseModel):
    id: UUID
    name: str | None = Field(
        description="Nulo 30 dias depois do envio, só em upload já excluído (retenção de 1.7)."
    )
    size: int
    state: UploadState
    availability: Availability
    sha256: str | None
    created_at: datetime
    ready_at: datetime | None
    expires_at: datetime


@router.post(
    "/uploads",
    status_code=201,
    operation_id="createUploads",
    responses=problem_responses(400, 413, 415, 429, 503),
    openapi_extra=command_openapi(CreateUploads),
)
def create_uploads(
    principal: WriteFiles,
    command: Annotated[CreateUploads, Depends(json_command(CreateUploads))],
    session: DbSession,
    request: Request,
) -> CreatedUploads:
    items = [(item.name, item.size) for item in command.items]
    try:
        with session.begin():
            views = create_intents(session, principal.owner_id, items)
    except InvalidUploadRequestError as error:
        raise _invalid_request(error) from None
    except AccountCapacityExceededError:
        raise account_storage_problem(session, principal.owner_id) from None
    except GlobalCapacityExceededError:
        raise environment_storage_problem() from None
    return CreatedUploads(items=[_created(view, request) for view in views])


@router.put(
    "/uploads/{upload_id}/content",
    operation_id="putUploadContent",
    responses=problem_responses(404, 408, 409, 410, 413, 415, 429),
    openapi_extra=OCTET_STREAM_BODY,
)
async def put_upload_content(
    upload_id: UUID,
    principal: WriteFiles,
    session: DbSession,
    storage: Storage,
    request: Request,
) -> ReceivedContent:
    if not is_octet_stream(request):
        raise ApiProblem(
            ProblemCode.UNSUPPORTED_MEDIA_TYPE, f"Envie o conteúdo como {OCTET_STREAM}."
        )
    owner_id = principal.owner_id
    slot_id = await anyio.to_thread.run_sync(_acquire_upload_slot, session, owner_id)
    try:
        chunks = request_body_chunks(request, timeouts_of(request))
        view = await anyio.to_thread.run_sync(
            receive_content, session, storage, owner_id, upload_id, chunks
        )
    except FilesError as error:
        raise _upload_problem(error) from None
    except TransferTimeoutError:
        raise ApiProblem(
            ProblemCode.REQUEST_TIMEOUT,
            "O envio passou do prazo; o upload falhou. Crie um novo upload e envie de novo.",
        ) from None
    except ClientDisconnect:
        # Ninguém lerá a resposta; o upload já ficou `failed` no serviço.
        raise ApiProblem(ProblemCode.BAD_REQUEST, "O envio foi interrompido.") from None
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(_release_slot, session, slot_id)
    # `ready` garante tamanho e hash (o serviço só conclui depois de persistir).
    if view.size_bytes is None or view.sha256 is None:
        raise RuntimeError("upload pronto sem tamanho ou hash")
    return ReceivedContent(
        id=view.id, state=view.state, size=view.size_bytes, sha256=view.sha256.hex()
    )


@router.get("/uploads/{upload_id}", operation_id="getUpload", responses=problem_responses(404))
def get_upload_details(upload_id: UUID, principal: ReadFiles, session: DbSession) -> UploadDetails:
    try:
        with session.begin():
            view = get_upload(session, principal.owner_id, upload_id)
    except FilesError as error:
        raise _upload_problem(error) from None
    return UploadDetails(
        id=view.id,
        name=view.original_name,
        size=view.expected_size,
        state=view.state,
        availability=view.availability,
        sha256=view.sha256.hex() if view.sha256 is not None else None,
        created_at=view.created_at,
        ready_at=view.ready_at,
        expires_at=view.expires_at,
    )


@router.delete(
    "/uploads/{upload_id}",
    status_code=202,
    operation_id="deleteUpload",
    responses=problem_responses(404),
    response_class=Response,
)
def delete_upload(upload_id: UUID, principal: WriteFiles, session: DbSession) -> Response:
    """Exclusão aceita; repetir também é 202 (a remoção física espera os usos ativos)."""
    try:
        with session.begin():
            request_deletion(session, principal.owner_id, upload_id)
    except FilesError as error:
        raise _upload_problem(error) from None
    return Response(status_code=202)


def _created(view: UploadView, request: Request) -> CreatedUpload:
    if view.original_name is None:
        # A intenção acabou de nascer com o nome; só a retenção o anula, 30 dias depois.
        raise RuntimeError(f"upload {view.id} recém-criado sem nome")
    return CreatedUpload(
        id=view.id,
        name=view.original_name,
        size=view.expected_size,
        state=view.state,
        expires_at=view.expires_at,
        content=ContentInstructions(
            method="PUT",
            url=request.app.url_path_for("put_upload_content", upload_id=str(view.id)),
            headers={"Content-Type": OCTET_STREAM},
        ),
    )


def _acquire_upload_slot(session: Session, owner_id: UUID) -> UUID:
    try:
        with session.begin():
            return acquire_transfer_slot(session, owner_id, TransferDirection.UPLOAD)
    except TransferLimitReachedError:
        raise transfer_limit_problem() from None


def _release_slot(session: Session, slot_id: UUID) -> None:
    with session.begin():
        release_transfer_slot(session, slot_id)


def _upload_problem(error: FilesError) -> ApiProblem:
    match error:
        case UploadNotFoundError():
            return ApiProblem(ProblemCode.NOT_FOUND, "Upload não encontrado.")
        case UploadGoneError():
            return ApiProblem(ProblemCode.GONE, "O upload expirou ou foi excluído.")
        case UploadStateConflictError(state=state):
            return ApiProblem(
                ProblemCode.STATE_CONFLICT, f"O upload está em {state.value} e não aceita conteúdo."
            )
        case UploadContentTooLargeError(expected_size=expected_size):
            return ApiProblem(
                ProblemCode.BODY_TOO_LARGE,
                f"O conteúdo passou dos {expected_size} bytes declarados; o upload falhou.",
            )
        case UploadSizeMismatchError(expected_size=expected_size):
            return _validation_problem(
                "body",
                "size_mismatch",
                f"O conteúdo tem menos que os {expected_size} bytes declarados; o upload falhou.",
            )
        case _:
            raise error


def _invalid_request(error: InvalidUploadRequestError) -> ApiProblem:
    return _validation_problem(_api_field(error.field), "invalid", error.reason)


def _api_field(service_field: str) -> str:
    """`items[3].expected_size` → `body.items.3.size`, no formato dos erros do Pydantic."""
    parts = service_field.replace("[", ".").replace("]", "").split(".")
    return ".".join(["body", *(_API_FIELD_NAMES.get(part, part) for part in parts)])


def _validation_problem(field: str, code: str, message: str) -> ApiProblem:
    return ApiProblem(
        ProblemCode.VALIDATION_FAILED,
        "A requisição tem campos inválidos; veja `errors`.",
        errors=[{"field": field, "code": code, "message": message}],
    )
