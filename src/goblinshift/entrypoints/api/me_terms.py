"""`POST /api/v1/me/terms:accept`: aceite dos termos vigentes pela própria conta (1.8; ADR 029).

Só sessão web, com CSRF: o aceite é um ato da pessoa no navegador, não de um script com chave.
O corpo traz só a versão que a pessoa leu, para confirmar que é a vigente (senão 409); o valor
gravado é o do servidor, e a conta é sempre a da credencial. Campo extra → 422, nada gravado.

Repetir é seguro: responde 200 com a data do primeiro aceite e não audita de novo.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from goblinshift.entrypoints.api.dependencies import (
    DbSession,
    Now,
    SessionPrincipal,
    client_ip,
    terms_version,
)
from goblinshift.entrypoints.api.json_command import command_openapi, json_command
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode, problem_responses
from goblinshift.identity import (
    AuditEventType,
    AuditOutcome,
    UnknownAccountError,
    accept_terms,
    record_audit_event,
)

router = APIRouter(tags=["me"])

# O limite do CHECK `app_user_terms_version`; o padrão recusa NUL e caracteres de controle.
MAX_VERSION_CHARS = 64
VERSION_PATTERN = r"^[A-Za-z0-9._-]+$"


class AcceptTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=MAX_VERSION_CHARS, pattern=VERSION_PATTERN)


class AcceptedTermsResponse(BaseModel):
    version: str
    accepted_at: datetime


@router.post(
    "/me/terms:accept",
    operation_id="acceptTerms",
    openapi_extra=command_openapi(AcceptTerms),
    responses=problem_responses(409),
)
def accept_current_terms(
    principal: SessionPrincipal,
    command: Annotated[AcceptTerms, Depends(json_command(AcceptTerms))],
    current_version: Annotated[str, Depends(terms_version)],
    session: DbSession,
    now: Now,
    request: Request,
) -> AcceptedTermsResponse:
    if command.version != current_version:
        raise ApiProblem(
            ProblemCode.STATE_CONFLICT,
            f"A versão vigente dos termos é {current_version}; leia-a antes de aceitar.",
        )
    try:
        with session.begin():
            accepted = accept_terms(session, principal.owner_id, version=current_version, now=now)
            if accepted.newly_accepted:
                record_audit_event(
                    session,
                    event=AuditEventType.TERMS_ACCEPTED,
                    user_id=principal.owner_id,
                    ip=client_ip(request),
                    outcome=AuditOutcome.SUCCESS,
                    now=now,
                )
    except UnknownAccountError:
        raise ApiProblem(
            ProblemCode.AUTHENTICATION_REQUIRED, "A sessão não vale mais para esta conta."
        ) from None
    return AcceptedTermsResponse(version=accepted.version, accepted_at=accepted.accepted_at)
