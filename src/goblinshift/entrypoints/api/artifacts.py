"""Rotas de artefato: metadados e download (docs/GoblinShiftPlan.md, 1.6, 1.7, 3.2 e 3.6).

O download revalida o acesso, registra o uso do objeto e reserva a vaga numa transação curta,
encerrada antes do primeiro byte; o uso e a vaga são devolvidos noutra transação curta quando a
resposta termina, inclusive se o cliente desconectar. A Session da requisição já está fechada
nesse momento (`scope="function"`), então o encerramento abre a sua pela factory.
"""

import logging
from datetime import datetime
from functools import partial
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.dependencies import DbSession, ReadFiles, Storage
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode, problem_responses
from goblinshift.entrypoints.api.transfers import (
    DOWNLOAD_PROTECTION_HEADERS,
    OCTET_STREAM,
    DownloadResponse,
    content_disposition,
    timeouts_of,
    transfer_limit_problem,
)
from goblinshift.files.artifacts import (
    DownloadTicket,
    finish_download,
    finish_missing_download,
    get_artifact,
    open_for_download,
    open_preview,
)
from goblinshift.files.errors import (
    ArtifactGoneError,
    ArtifactNotFoundError,
    FilesError,
    TransferLimitReachedError,
)
from goblinshift.files.states import ArtifactRole, Availability, TransferDirection
from goblinshift.files.storage import ObjectMissingError
from goblinshift.files.transfers import acquire_transfer_slot, release_transfer_slot
from goblinshift.platform.db import unit_of_work

logger = logging.getLogger(__name__)

router = APIRouter(tags=["artifacts"])

JPEG_MEDIA_TYPE = "image/jpeg"
_JPEG_SUFFIX = ".jpg"
# A coleta do worker já limita o preview a 512 KiB (JPEG) e 64 KiB (CSV); este teto só impede
# carregar na memória algo maior que isso se o banco e o storage divergirem.
PREVIEW_READ_MAX_BYTES = 1024 * 1024


class ArtifactDetails(BaseModel):
    id: UUID
    role: ArtifactRole
    size: int
    sha256: str
    expires_at: datetime
    availability: Availability


@router.get(
    "/artifacts/{artifact_id}", operation_id="getArtifact", responses=problem_responses(404)
)
def get_artifact_details(
    artifact_id: UUID, principal: ReadFiles, session: DbSession
) -> ArtifactDetails:
    try:
        with session.begin():
            view = get_artifact(session, principal.owner_id, artifact_id)
    except FilesError as error:
        raise _artifact_problem(error) from None
    return ArtifactDetails(
        id=view.id,
        role=view.role,
        size=view.size_bytes,
        sha256=view.sha256.hex(),
        expires_at=view.expires_at,
        availability=view.availability,
    )


@router.get(
    "/artifacts/{artifact_id}/download",
    operation_id="downloadArtifact",
    status_code=200,
    response_class=DownloadResponse,
    responses={
        200: {"content": {OCTET_STREAM: {"schema": {"type": "string", "format": "binary"}}}},
        **problem_responses(404, 410, 429),
    },
)
def download_artifact(
    artifact_id: UUID,
    principal: ReadFiles,
    session: DbSession,
    storage: Storage,
    request: Request,
) -> DownloadResponse:
    try:
        # Uma transação só: sem vaga, o rollback desfaz também o uso registrado.
        with session.begin():
            ticket = open_for_download(session, principal.owner_id, artifact_id)
            slot_id = acquire_transfer_slot(session, principal.owner_id, TransferDirection.DOWNLOAD)
    except TransferLimitReachedError:
        raise transfer_limit_problem() from None
    except FilesError as error:
        raise _artifact_problem(error) from None

    factory: sessionmaker[Session] = request.app.state.session_factory
    finish = partial(_finish_download, factory, ticket, slot_id)
    try:
        file = storage.open(ticket.storage_key)
    except ObjectMissingError:
        logger.error("download: objeto do artefato %s ausente do storage", artifact_id)
        with unit_of_work(factory) as finishing:
            release_transfer_slot(finishing, slot_id)
            finish_missing_download(finishing, ticket)
        raise _artifact_problem(ArtifactGoneError()) from None
    except BaseException:
        finish()
        raise
    return DownloadResponse(
        file,
        size=ticket.size_bytes,
        # Saídas em grupo guardam o caminho ("images/foto.jpg"); o anexo leva só o nome (ADR 019).
        filename=PurePosixPath(ticket.file_name).name,
        timeouts=timeouts_of(request),
        on_close=finish,
    )


@router.get(
    "/artifacts/{artifact_id}/preview",
    operation_id="getArtifactPreview",
    status_code=200,
    response_class=Response,
    responses={
        200: {
            "description": "Imagem reduzida (`image/jpeg`) ou amostra CSV escapada, como bytes.",
            "content": {
                JPEG_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}},
                OCTET_STREAM: {"schema": {"type": "string", "format": "binary"}},
            },
        },
        **problem_responses(404, 410),
    },
)
def get_artifact_preview(
    artifact_id: UUID,
    principal: ReadFiles,
    session: DbSession,
    storage: Storage,
    request: Request,
) -> Response:
    """Preview de uma saída, quando a receita gerou um (2.2; 3.6).

    Só derivados gerados pelo serviço chegam aqui: a imagem reduzida sai com o tipo real; a
    amostra CSV, como bytes opacos. Os dois com `nosniff`, CSP `sandbox` e `attachment`. Sem vaga
    de transferência: o preview tem no máximo 512 KiB e é lido inteiro antes de responder.
    """
    try:
        with session.begin():
            ticket = open_preview(session, principal.owner_id, artifact_id)
    except FilesError as error:
        raise _artifact_problem(error) from None
    factory: sessionmaker[Session] = request.app.state.session_factory
    try:
        with storage.open(ticket.storage_key) as file:
            content = file.read(PREVIEW_READ_MAX_BYTES + 1)
    except ObjectMissingError:
        logger.error("preview: objeto do artefato %s ausente do storage", ticket.artifact_id)
        with unit_of_work(factory) as finishing:
            finish_missing_download(finishing, ticket)
        raise _artifact_problem(ArtifactGoneError()) from None
    except BaseException:
        _finish_preview(factory, ticket)
        raise
    _finish_preview(factory, ticket)
    if len(content) != ticket.size_bytes:
        raise RuntimeError(f"preview {ticket.artifact_id} com tamanho diferente do registrado")
    name = PurePosixPath(ticket.file_name).name
    return Response(
        content=content,
        media_type=JPEG_MEDIA_TYPE if name.endswith(_JPEG_SUFFIX) else OCTET_STREAM,
        headers={"Content-Disposition": content_disposition(name), **DOWNLOAD_PROTECTION_HEADERS},
    )


def _finish_preview(factory: sessionmaker[Session], ticket: DownloadTicket) -> None:
    with unit_of_work(factory) as session:
        finish_download(session, ticket)


def _finish_download(factory: sessionmaker[Session], ticket: DownloadTicket, slot_id: UUID) -> None:
    """Encerra o uso e devolve a vaga; repetir é no-op nas duas operações.

    Se o banco falhar aqui, o erro sobe e é registrado com o `request_id`; a vaga vence sozinha
    e o uso aberto só atrasa a remoção física do objeto.
    """
    with unit_of_work(factory) as session:
        finish_download(session, ticket)
        release_transfer_slot(session, slot_id)


def _artifact_problem(error: FilesError) -> ApiProblem:
    match error:
        case ArtifactNotFoundError():
            return ApiProblem(ProblemCode.NOT_FOUND, "Artefato não encontrado.")
        case ArtifactGoneError():
            return ApiProblem(
                ProblemCode.GONE, "O artefato expirou, foi excluído ou o arquivo se perdeu."
            )
        case _:
            raise error
