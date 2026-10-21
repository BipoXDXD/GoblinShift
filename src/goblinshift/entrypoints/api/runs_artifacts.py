"""`DELETE /api/v1/runs/{run_id}/artifacts` (docs/GoblinShiftPlan.md, 1.6, 1.7 e 3.6).

- `202`: as saídas, o relatório, o manifesto e o pacote ficam indisponíveis na hora; os arquivos
  saem do disco pela manutenção, quando nenhum download aberto os usa. Repetir também é `202`.
- `409`: a execução ainda não terminou e pode publicar.
- `404`: inexistente ou de outra conta, com a mesma resposta.

Os originais (uploads) não mudam. Exige `files:write`, como excluir um upload.
"""

from uuid import UUID

from fastapi import APIRouter, Response

from goblinshift.entrypoints.api.dependencies import DbSession, WriteFiles
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode, problem_responses
from goblinshift.runs.output_deletion import RunNotTerminalError, delete_run_outputs
from goblinshift.runs.queries import RunNotFoundError

router = APIRouter(tags=["runs"])


@router.delete(
    "/runs/{run_id}/artifacts",
    status_code=202,
    operation_id="deleteRunArtifacts",
    responses=problem_responses(404, 409),
    response_class=Response,
)
def delete_run_artifacts(run_id: UUID, principal: WriteFiles, session: DbSession) -> Response:
    try:
        with session.begin():
            delete_run_outputs(session, principal.owner_id, run_id)
    except RunNotFoundError:
        raise ApiProblem(ProblemCode.NOT_FOUND, "Execução não encontrada.") from None
    except RunNotTerminalError:
        raise ApiProblem(
            ProblemCode.STATE_CONFLICT,
            "A execução ainda não terminou; as saídas só podem ser excluídas depois do fim.",
        ) from None
    return Response(status_code=202)
