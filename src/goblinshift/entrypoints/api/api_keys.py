"""`GET`, `POST /api-keys` e `DELETE /api-keys/{key_id}`: chaves pela sessão web (Frente C).

Só sessão (`SessionPrincipal`); emitir exige autenticação recente (`RecentSessionPrincipal`).
Ver docs/GoblinShiftPlan.md, 1.6 e 3.6.

- `POST`: corpo estrito (`scopes`, `days`); o segredo aparece só nesta resposta, com
  `Cache-Control: no-store`. Com `MAX_ACTIVE_KEYS` ativas, ou `MAX_STORED_KEYS` guardadas
  contando revogadas e vencidas, → 409, cada um com a sua mensagem. `Idempotency-Key` opcional
  (#442, 57b): repetida na mesma conta, → 409 com o id da chave que ela já emitiu, mesmo revogada;
  o segredo não volta, porque não é guardado.
- `GET`: metadados, nunca o segredo nem o hash.
- `DELETE`: chave de outra conta ou inexistente → o mesmo 404; revogar de novo → 204.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from goblinshift.entrypoints.api.dependencies import (
    DbSession,
    Now,
    RecentSessionPrincipal,
    SessionPrincipal,
)
from goblinshift.entrypoints.api.idempotency import (
    idempotency_key_parameter,
    parse_idempotency_header,
)
from goblinshift.entrypoints.api.json_command import command_openapi, json_command
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode, problem_responses
from goblinshift.identity import (
    DEFAULT_KEY_DAYS,
    KEY_RETENTION,
    MAX_ACTIVE_KEYS,
    MAX_KEY_DAYS,
    MAX_STORED_KEYS,
    ActiveKeyLimitError,
    AuditActor,
    AuditEventType,
    AuditOutcome,
    KeyAlreadyIssuedError,
    KeySummary,
    Scope,
    SessionNoLongerValidError,
    StoredKeyLimitError,
    UnknownAccountError,
    issue_key_for_session,
    list_keys,
    record_audit_event,
    revoke_owned_key,
)
from goblinshift.identity.keys import MIN_KEY_DAYS

router = APIRouter(tags=["api-keys"])


class CreateApiKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopes: list[Scope] = Field(min_length=1, max_length=len(Scope))
    days: int = Field(default=DEFAULT_KEY_DAYS, ge=MIN_KEY_DAYS, le=MAX_KEY_DAYS)


class CreatedApiKey(BaseModel):
    id: UUID
    public_id: str
    # Exibido uma única vez: nem o servidor o guarda.
    token: str
    scopes: list[Scope]
    expires_at: datetime


class ApiKeyResponse(BaseModel):
    id: UUID
    public_id: str
    scopes: list[Scope]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyList(BaseModel):
    data: list[ApiKeyResponse]


@router.get("/api-keys", operation_id="listApiKeys")
def list_api_keys(principal: SessionPrincipal, session: DbSession, now: Now) -> ApiKeyList:
    with session.begin():
        keys = list_keys(session, principal.owner_id, now)
    return ApiKeyList(data=[_key_response(key) for key in keys])


def _create_api_key_openapi() -> dict[str, Any]:
    idempotency_key = idempotency_key_parameter(
        required=False,
        description=(
            "Opcional. Repetida na mesma conta, responde 409 com o id da chave que ela já emitiu;"
            " o segredo só aparece na primeira resposta."
        ),
    )
    return {**command_openapi(CreateApiKey), "parameters": [idempotency_key]}


@router.post(
    "/api-keys",
    operation_id="createApiKey",
    status_code=201,
    openapi_extra=_create_api_key_openapi(),
    responses=problem_responses(400, 409, 413, 415),
)
def create_api_key(
    principal: RecentSessionPrincipal,
    command: Annotated[CreateApiKey, Depends(json_command(CreateApiKey))],
    session: DbSession,
    now: Now,
    request: Request,
    response: Response,
) -> CreatedApiKey:
    if principal.session_id is None:
        raise RuntimeError("Principal de sessão sem session_id")
    # Lida à mão e publicada em `_create_api_key_openapi` (motivo em `idempotency`).
    idem_key = parse_idempotency_header(request)
    scopes = frozenset(command.scopes)
    try:
        with session.begin():
            issued = issue_key_for_session(
                session,
                owner_id=principal.owner_id,
                session_id=principal.session_id,
                scopes=scopes,
                days=command.days,
                now=now,
                idem_key=idem_key,
            )
            _audit(session, AuditEventType.KEY_ISSUED, principal.owner_id, request, now)
    except KeyAlreadyIssuedError as error:
        raise ApiProblem(
            ProblemCode.STATE_CONFLICT,
            f"Esta Idempotency-Key já emitiu a chave {error.public_id} (id {error.key_id}); o"
            " segredo só aparece na primeira resposta. Revogue-a se ele se perdeu.",
        ) from None
    except ActiveKeyLimitError:
        raise ApiProblem(
            ProblemCode.STATE_CONFLICT,
            f"A conta já tem {MAX_ACTIVE_KEYS} chaves ativas; revogue uma antes de criar outra.",
        ) from None
    except StoredKeyLimitError:
        # Revogar não abre vaga: a revogada só sai da tabela com a retenção.
        raise ApiProblem(
            ProblemCode.STATE_CONFLICT,
            f"A conta já tem {MAX_STORED_KEYS} chaves guardadas, contando as revogadas e vencidas;"
            f" elas saem {KEY_RETENTION.days} dias depois do fim, e então dá para criar outra.",
        ) from None
    except UnknownAccountError, SessionNoLongerValidError:
        # Bloqueio, exclusão ou logout chegou depois da autenticação: a credencial deixou de valer.
        raise ApiProblem(
            ProblemCode.AUTHENTICATION_REQUIRED, "A sessão não vale mais para esta conta."
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return CreatedApiKey(
        id=issued.id,
        public_id=issued.public_id,
        token=issued.token,
        scopes=sorted(scopes),
        expires_at=issued.expires_at,
    )


@router.delete(
    "/api-keys/{key_id}",
    operation_id="revokeApiKey",
    status_code=204,
    responses=problem_responses(404),
)
def revoke_api_key(
    key_id: UUID, principal: SessionPrincipal, session: DbSession, now: Now, request: Request
) -> Response:
    with session.begin():
        found = revoke_owned_key(session, principal.owner_id, key_id, now)
        if found:
            _audit(session, AuditEventType.KEY_REVOKED, principal.owner_id, request, now)
    if not found:
        raise ApiProblem(ProblemCode.NOT_FOUND, "Chave não encontrada.")
    return Response(status_code=204)


def _key_response(key: KeySummary) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=key.id,
        public_id=key.public_id,
        scopes=sorted(key.scopes),
        created_at=key.created_at,
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
        revoked_at=key.revoked_at,
    )


def _audit(
    session: Session, event: AuditEventType, owner_id: UUID, request: Request, now: datetime
) -> None:
    record_audit_event(
        session,
        event=event,
        user_id=owner_id,
        ip=request.client.host if request.client else None,
        outcome=AuditOutcome.SUCCESS,
        now=now,
        actor=AuditActor.USER,
    )
