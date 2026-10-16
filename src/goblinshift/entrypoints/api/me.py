"""`GET /api/v1/me`: a conta da credencial, os limites, o consumo das cotas e o aceite dos termos
(1.6, 1.7 e 1.8). Os limites são os da conta: o padrão das settings com o override do
administrador; o consumo inclui o tempo de executor (1.8).

Leitura numa transação só, com o relógio do banco, o mesmo da aceitação: o consumo mostrado é
o que a próxima aceitação vai contar. Saída por allowlist de campos, só da conta da credencial.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from goblinshift import capacity
from goblinshift.entrypoints.api.dependencies import CurrentPrincipal, DbSession, terms_version
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.files.limits import (
    MAX_BATCH_BYTES,
    MAX_BATCH_FILES,
    MAX_FILE_BYTES,
    MAX_TRANSFERS_PER_ACCOUNT,
    MAX_VIDEO_FILE_BYTES,
)
from goblinshift.identity import Scope, find_account
from goblinshift.runs.quotas import (
    QuotaLimits,
    active_run_id,
    daily_usage,
    executor_time_ms,
    quota_for,
)
from goblinshift.runs.store import db_now

router = APIRouter()

# Uma execução não terminal por conta (1.7, "Processamento").
ACTIVE_RUNS_PER_ACCOUNT = 1


class Limits(BaseModel):
    runs_per_24h: int
    input_bytes_per_24h: int
    storage_bytes: int
    active_runs: int
    file_bytes: int
    # Vídeo (.mp4, .mov, .webm), aceito só pelo Media Mill (1.7, ADR 036).
    video_file_bytes: int
    batch_files: int
    batch_bytes: int
    concurrent_uploads: int
    concurrent_downloads: int


class Usage(BaseModel):
    runs_last_24h: int
    input_bytes_last_24h: int
    storage_reserved_bytes: int
    active_run_id: UUID | None
    # Quando a execução mais antiga sai da janela de 24 h; nulo sem execuções na janela.
    daily_quota_resets_at: datetime | None
    executor_ms_last_24h: int = Field(
        description="Tempo de executor das tentativas que começaram nas últimas 24 h (1.8)."
    )


class Terms(BaseModel):
    """A versão vigente e a última aceita; `acceptance_required` bloqueia as demais rotas."""

    current_version: str
    accepted_version: str | None
    accepted_at: datetime | None
    acceptance_required: bool


class MeResponse(BaseModel):
    id: UUID
    display_name: str
    scopes: list[Scope]
    via: Literal["session", "api_key"]
    limits: Limits
    usage: Usage
    terms: Terms


@router.get("/me", operation_id="getMe")
def get_me(
    request: Request,
    principal: CurrentPrincipal,
    session: DbSession,
    current_version: Annotated[str, Depends(terms_version)],
) -> MeResponse:
    owner_id = principal.owner_id
    defaults: QuotaLimits = request.app.state.quota_defaults
    with session.begin():
        now = db_now(session)
        account = find_account(session, owner_id)
        storage = capacity.usage(session, owner_id)
        daily = daily_usage(session, owner_id, now)
        quota = quota_for(session, owner_id, defaults)
        executor_ms = executor_time_ms(session, owner_id, now)
        active = active_run_id(session, owner_id)
    # A conta não é apagada com chave ativa (FK RESTRICT); se sumir entre as duas
    # transações, a credencial deixou de valer.
    if account is None:
        raise ApiProblem(ProblemCode.AUTHENTICATION_REQUIRED, "A conta da chave não existe mais.")
    return MeResponse(
        id=account.id,
        display_name=account.display_name,
        scopes=sorted(principal.scopes),
        via=principal.via,
        limits=Limits(
            runs_per_24h=quota.runs_per_24h,
            input_bytes_per_24h=quota.input_bytes_per_24h,
            storage_bytes=storage.storage_max_bytes,
            active_runs=ACTIVE_RUNS_PER_ACCOUNT,
            file_bytes=MAX_FILE_BYTES,
            video_file_bytes=MAX_VIDEO_FILE_BYTES,
            batch_files=MAX_BATCH_FILES,
            batch_bytes=MAX_BATCH_BYTES,
            concurrent_uploads=MAX_TRANSFERS_PER_ACCOUNT,
            concurrent_downloads=MAX_TRANSFERS_PER_ACCOUNT,
        ),
        usage=Usage(
            runs_last_24h=daily.runs,
            input_bytes_last_24h=daily.input_bytes,
            storage_reserved_bytes=storage.storage_reserved_bytes,
            active_run_id=active,
            daily_quota_resets_at=daily.resets_at(),
            executor_ms_last_24h=executor_ms,
        ),
        terms=Terms(
            current_version=current_version,
            accepted_version=account.accepted_terms.version if account.accepted_terms else None,
            accepted_at=account.accepted_terms.accepted_at if account.accepted_terms else None,
            acceptance_required=account.needs_terms_acceptance(current_version),
        ),
    )
