"""`GET /api/v1/me`: a conta da credencial, os limites e o consumo das cotas (1.6 e 1.7).

Leitura numa transação só, com o relógio do banco, o mesmo da aceitação: o consumo mostrado é
o que a próxima aceitação vai contar. Saída por allowlist de campos, só da conta da credencial.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from goblinshift import capacity
from goblinshift.entrypoints.api.dependencies import CurrentPrincipal, DbSession
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.files.limits import (
    MAX_BATCH_BYTES,
    MAX_BATCH_FILES,
    MAX_FILE_BYTES,
    MAX_TRANSFERS_PER_ACCOUNT,
)
from goblinshift.identity import Scope, find_account
from goblinshift.runs.quotas import (
    MAX_INPUT_BYTES_PER_WINDOW,
    MAX_RUNS_PER_WINDOW,
    active_run_id,
    daily_usage,
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


class MeResponse(BaseModel):
    id: UUID
    display_name: str
    scopes: list[Scope]
    via: Literal["session", "api_key"]
    limits: Limits
    usage: Usage


@router.get("/me", operation_id="getMe")
def get_me(principal: CurrentPrincipal, session: DbSession) -> MeResponse:
    owner_id = principal.owner_id
    with session.begin():
        account = find_account(session, owner_id)
        storage = capacity.usage(session, owner_id)
        daily = daily_usage(session, owner_id, db_now(session))
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
            runs_per_24h=MAX_RUNS_PER_WINDOW,
            input_bytes_per_24h=MAX_INPUT_BYTES_PER_WINDOW,
            storage_bytes=storage.storage_max_bytes,
            active_runs=ACTIVE_RUNS_PER_ACCOUNT,
            file_bytes=MAX_FILE_BYTES,
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
        ),
    )
