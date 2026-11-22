"""Recusa parâmetro de consulta que a rota de `/api` não declara (#442, item 44a).

Sem isto, o FastAPI ignora a query desconhecida: `?validate_onl=true` criava a Run de
verdade e `?state=failed` listava tudo como se o filtro tivesse valido.
"""

from fastapi import Request
from fastapi.dependencies.models import Dependant
from fastapi.dependencies.utils import get_validation_alias
from fastapi.routing import APIRoute

from goblinshift.entrypoints.api.dependencies import CurrentPrincipal
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode

# O erro nunca cita o nome recebido: a query é texto do cliente e voltaria refletida.
UNKNOWN_QUERY_ERROR = {
    "field": "query",
    "code": "extra_forbidden",
    "message": "parâmetro de consulta não aceito",
}


def reject_unknown_query(request: Request, _authenticated: CurrentPrincipal) -> None:
    """Dependência do roteador de `/api`: 422 se a query traz nome fora do contrato da rota.

    Depende da autenticação para que quem não tem credencial válida receba 401, nunca 422. Toda
    rota de `/api` já autentica, e o FastAPI resolve `authenticate` uma vez por requisição: a
    rota reaproveita o mesmo Principal, sem contar duas vezes no limite por conta.
    """
    route: APIRoute = request.scope["route"]
    if not set(request.query_params) <= declared_query_names(route.dependant):
        raise ApiProblem(
            ProblemCode.VALIDATION_FAILED,
            "A requisição tem parâmetro de consulta não aceito.",
            errors=[UNKNOWN_QUERY_ERROR],
        )


def declared_query_names(dependant: Dependant) -> set[str]:
    """Nomes (pelo `alias`) das queries da rota e de todas as suas dependências."""
    names = {get_validation_alias(field) for field in dependant.query_params}
    for sub_dependant in dependant.dependencies:
        names |= declared_query_names(sub_dependant)
    return names
