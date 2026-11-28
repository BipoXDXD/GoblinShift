"""`GET /me/export`: os dados da própria conta em JSON para baixar (#442, 32c e 52d; LGPD, art. 19).

Só sessão com autenticação recente (`RecentSessionPrincipal`), como `DELETE /me`: quem pega um
computador com a sessão aberta não leva o histórico da conta. Limite de requisições da classe de
criação de execução (`RUN_CREATE`, 10/min), porque a leitura junta várias tabelas.

Uma transação `REPEATABLE READ READ ONLY`: todas as partes saem do mesmo instante. A saída é por
allowlist de campos e só da conta da credencial; nunca o `subject` do provedor, hash de chave ou de
sessão, nem o id da sessão. Os nomes de arquivo por execução seguem em `GET /runs/{id}`.
"""

from datetime import datetime
from ipaddress import IPv4Network, IPv6Network
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from goblinshift.entrypoints.api.dependencies import DbSession, RecentSessionPrincipal
from goblinshift.entrypoints.api.me import Limits, Usage, read_limits_and_usage
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.identity import (
    AuditEventType,
    AuditOutcome,
    Scope,
    find_account,
    list_audit_events,
    list_keys,
    list_sessions,
)
from goblinshift.presets.store import list_presets
from goblinshift.runs.lifecycle import Status
from goblinshift.runs.queries import export_runs
from goblinshift.runs.quotas import QuotaLimits
from goblinshift.runs.store import db_now

router = APIRouter(tags=["me"])

EXPORT_FILE_NAME = "goblinshift-conta.json"
EXPORT_HEADERS = {
    "Content-Disposition": f'attachment; filename="{EXPORT_FILE_NAME}"',
    "Cache-Control": "no-store",
}


class ExportedAccount(BaseModel):
    id: UUID
    issuer: str
    display_name: str
    status: str
    created_at: datetime


class ExportedTerms(BaseModel):
    accepted_version: str | None
    accepted_at: datetime | None


class ExportedApiKey(BaseModel):
    """Metadados da chave; o segredo nunca é guardado, e o hash não sai."""

    public_id: str
    scopes: list[Scope]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ExportedSession(BaseModel):
    """Datas da sessão, sem id nem hash."""

    created_at: datetime
    authenticated_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None


class ExportedSecurityEvent(BaseModel):
    event_type: AuditEventType
    outcome: AuditOutcome
    occurred_at: datetime
    ip_prefix: str | None = Field(description="A rede do cliente: /24 em IPv4, /48 em IPv6.")
    details: dict[str, Any] | None


class ExportedRunSummary(BaseModel):
    id: UUID
    recipe_id: str
    recipe_version: int
    status: Status
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    processed: int
    total: int
    summary: dict[str, int] | None
    parameters: dict[str, Any] | None
    preset_id: UUID | None


class ExportedPreset(BaseModel):
    id: UUID
    name: str
    recipe_id: str
    recipe_version: int
    parameters: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AccountExport(BaseModel):
    generated_at: datetime
    account: ExportedAccount
    terms: ExportedTerms
    limits: Limits
    usage: Usage
    api_keys: list[ExportedApiKey]
    sessions: list[ExportedSession]
    security_events: list[ExportedSecurityEvent]
    runs: list[ExportedRunSummary]
    presets: list[ExportedPreset]


# A rota devolve a `Response` pronta (headers do anexo); `response_model` documenta o corpo.
@router.get("/me/export", operation_id="exportMe", response_model=AccountExport)
def export_me(request: Request, principal: RecentSessionPrincipal, session: DbSession) -> Response:
    owner_id = principal.owner_id
    defaults: QuotaLimits = request.app.state.quota_defaults
    with session.begin():
        # Primeiro comando da transação: depois dele o Postgres recusa a troca de isolamento.
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        generated_at = db_now(session)
        account = find_account(session, owner_id)
        limits, usage = read_limits_and_usage(session, owner_id, defaults, include_active_run=True)
        keys = list_keys(session, owner_id, generated_at, limit=None)
        sessions = list_sessions(session, owner_id)
        events = list_audit_events(session, owner_id)
        runs = export_runs(session, owner_id)
        presets = list_presets(session, owner_id)
    # A sessão autenticou nesta requisição; a conta só some se a purga correr no meio.
    if account is None:
        raise ApiProblem(
            ProblemCode.AUTHENTICATION_REQUIRED, "A sessão não vale mais para esta conta."
        )
    export = AccountExport(
        generated_at=generated_at,
        account=ExportedAccount(
            id=account.id,
            issuer=account.issuer,
            display_name=account.display_name,
            status=account.status,
            created_at=account.created_at,
        ),
        terms=ExportedTerms(
            accepted_version=account.accepted_terms.version if account.accepted_terms else None,
            accepted_at=account.accepted_terms.accepted_at if account.accepted_terms else None,
        ),
        limits=limits,
        usage=usage,
        api_keys=[
            ExportedApiKey(
                public_id=key.public_id,
                scopes=sorted(key.scopes),
                created_at=key.created_at,
                expires_at=key.expires_at,
                last_used_at=key.last_used_at,
                revoked_at=key.revoked_at,
            )
            for key in keys
        ],
        sessions=[
            ExportedSession(
                created_at=item.created_at,
                authenticated_at=item.authenticated_at,
                last_seen_at=item.last_seen_at,
                idle_expires_at=item.idle_expires_at,
                absolute_expires_at=item.absolute_expires_at,
                revoked_at=item.revoked_at,
            )
            for item in sessions
        ],
        security_events=[
            ExportedSecurityEvent(
                event_type=event.event_type,
                outcome=event.outcome,
                occurred_at=event.occurred_at,
                ip_prefix=_network(event.ip_prefix),
                details=event.details,
            )
            for event in events
        ],
        runs=[
            ExportedRunSummary(
                id=item.id,
                recipe_id=item.recipe_id,
                recipe_version=item.recipe_version,
                status=item.status,
                error_code=item.error_code,
                created_at=item.created_at,
                started_at=item.started_at,
                finished_at=item.finished_at,
                processed=item.processed,
                total=item.total,
                summary=item.summary,
                parameters=item.parameters,
                preset_id=item.preset_id,
            )
            for item in runs
        ],
        presets=[
            ExportedPreset(
                id=item.id,
                name=item.name,
                recipe_id=item.recipe_id,
                recipe_version=item.recipe_version,
                parameters=item.parameters,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in presets
        ],
    )
    return Response(
        content=export.model_dump_json(),
        media_type="application/json",
        headers=EXPORT_HEADERS,
    )


def _network(prefix: IPv4Network | IPv6Network | None) -> str | None:
    return None if prefix is None else str(prefix)
