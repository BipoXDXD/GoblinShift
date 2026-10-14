"""`GET /api/v1/me`: quem é a conta da chave usada (docs/GoblinShiftPlan.md, 1.6).

Limites técnicos e consumo das cotas entram com as cotas, na fatia 6.
"""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from goblinshift.entrypoints.api.dependencies import CurrentPrincipal, DbSession
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.identity import Scope, find_account

router = APIRouter()


class MeResponse(BaseModel):
    id: UUID
    display_name: str
    scopes: list[Scope]


@router.get("/me", operation_id="getMe")
def get_me(principal: CurrentPrincipal, session: DbSession) -> MeResponse:
    with session.begin():
        account = find_account(session, principal.owner_id)
    # A conta não é apagada com chave ativa (FK RESTRICT); se sumir entre as duas
    # transações, a credencial deixou de valer.
    if account is None:
        raise ApiProblem(ProblemCode.AUTHENTICATION_REQUIRED, "A conta da chave não existe mais.")
    return MeResponse(
        id=account.id,
        display_name=account.display_name,
        scopes=sorted(principal.scopes),
    )
