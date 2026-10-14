"""`POST /api/v1/runs/{run_id}/cancel` (docs/GoblinShiftPlan.md, 1.6 e 3.5).

- `202`: a parada foi pedida e o executor ainda está sendo encerrado; consulte a execução até
  ela ficar `canceled`.
- `200`: a execução já está num estado terminal, devolvido como é (inclusive um sucesso
  confirmado antes do pedido, ou o `canceled` de uma execução que ainda estava na fila).
- `404`: inexistente ou de outra conta, com a mesma resposta.

Sem corpo de entrada: a ação é o próprio recurso `:cancel`. Repetir o pedido é seguro.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.dependencies import Storage, require_scope
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode, problem_responses
from goblinshift.identity import Principal, Scope
from goblinshift.runs.cancellation import RunNotFoundError, cancel_run

router = APIRouter(tags=["runs"])

WriteRuns = Annotated[Principal, Depends(require_scope(Scope.RUNS_WRITE))]
STATUS_STOPPING = 202
STATUS_TERMINAL = 200


class CancelResponse(BaseModel):
    id: UUID
    status: str
    error_code: str | None


@router.post(
    "/runs/{run_id}/cancel",
    operation_id="cancelRun",
    status_code=STATUS_STOPPING,
    responses={STATUS_TERMINAL: {"model": CancelResponse}, **problem_responses(404)},
)
def cancel(
    run_id: UUID, principal: WriteRuns, storage: Storage, request: Request, response: Response
) -> CancelResponse:
    # O cancelamento abre as próprias transações curtas (decisão e liberação de usos), com
    # retentativa em deadlock; por isso recebe a fábrica de Sessions, não a Session da rota.
    factory: sessionmaker[Session] = request.app.state.session_factory
    try:
        result = cancel_run(factory, storage, principal.owner_id, run_id)
    except RunNotFoundError:
        raise ApiProblem(ProblemCode.NOT_FOUND, "Execução não encontrada.") from None
    response.status_code = STATUS_TERMINAL if result.is_terminal else STATUS_STOPPING
    return CancelResponse(id=result.run_id, status=result.status, error_code=result.error_code)
