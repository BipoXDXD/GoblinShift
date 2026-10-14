"""Dependências comuns às rotas: Session, relógio, autenticação e escopo.

Ver docs/GoblinShiftPlan.md, 3.2 (Session com `scope="function"`) e 3.6 (chaves de API).

Uso numa rota:

    @router.post("/uploads")
    def create_uploads(
        principal: Annotated[Principal, Depends(require_scope(Scope.FILES_WRITE))],
        session: DbSession,
    ) -> ...:
        with session.begin():
            ...
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.entrypoints.api.wide_event import record_principal
from goblinshift.files.storage import FileStorage
from goblinshift.identity import Principal, Scope, authenticate_api_key

BEARER_SCHEME = "bearer"
_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def get_session(request: Request) -> Iterator[Session]:
    """Uma Session por requisição; os serviços abrem transações curtas com `session.begin()`.

    Registrada com `scope="function"` (FastAPI ≥ 0.121): a Session fecha quando a rota
    termina, antes de a resposta ser enviada, e nunca fica *idle in transaction* durante
    um download por streaming.
    """
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


DbSession = Annotated[Session, Depends(get_session, scope="function")]


def get_storage(request: Request) -> FileStorage:
    storage: FileStorage = request.app.state.storage
    return storage


Storage = Annotated[FileStorage, Depends(get_storage)]


def current_time() -> datetime:
    """O relógio da API; os testes o substituem por `app.dependency_overrides`."""
    return datetime.now(UTC)


Now = Annotated[datetime, Depends(current_time)]


def authenticate(request: Request, session: DbSession, now: Now) -> Principal:
    """`Authorization: Bearer <chave>` → Principal; ausente ou inválida → 401.

    Mesma resposta para chave malformada, desconhecida, expirada, revogada ou de conta
    bloqueada: o cliente não aprende qual foi o caso, o log do servidor registra.
    """
    token = _bearer_token(request)
    with session.begin():
        principal = authenticate_api_key(session, token, now)
    if principal is None:
        raise _unauthenticated("A chave de API é inválida, expirou ou foi revogada.")
    record_principal(principal.owner_id, principal.via)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(authenticate)]


def require_scope(scope: Scope) -> Callable[[Principal], Principal]:
    """Dependência que autentica e exige `scope`; sem ele → 403 `INSUFFICIENT_SCOPE`."""

    def check_scope(principal: CurrentPrincipal) -> Principal:
        if scope not in principal.scopes:
            raise ApiProblem(
                ProblemCode.INSUFFICIENT_SCOPE, f"A chave não tem o escopo {scope.value}."
            )
        return principal

    return check_scope


def require_session(principal: CurrentPrincipal) -> Principal:
    """Só sessão web (`/api-keys`, `DELETE /me`, `logout-all`); chave → 403 `FORBIDDEN`.

    Contrato fixado na Fase 0 da fatia 6; o corpo é da Frente B.
    """
    raise NotImplementedError("Frente B da fatia 6")


SessionPrincipal = Annotated[Principal, Depends(require_session)]


def require_recent_session(principal: SessionPrincipal, now: Now) -> Principal:
    """Sessão com login no provedor há no máximo `RECENT_AUTH_WINDOW` (10 min, fronteira
    inclusiva); senão 401 `REAUTH_REQUIRED`, auditado.

    Contrato fixado na Fase 0 da fatia 6; o corpo é da Frente B.
    """
    raise NotImplementedError("Frente B da fatia 6")


RecentSessionPrincipal = Annotated[Principal, Depends(require_recent_session)]


def _bearer_token(request: Request) -> str:
    values = request.headers.getlist("authorization")
    if not values:
        raise _unauthenticated("Envie a chave de API no header Authorization: Bearer.")
    # Dois headers Authorization são ambíguos: nenhum deles é escolhido.
    if len(values) > 1:
        raise _unauthenticated("Envie um único header Authorization.")
    scheme, _, token = values[0].partition(" ")
    if scheme.lower() != BEARER_SCHEME or not token.strip():
        raise _unauthenticated("Envie a chave de API no header Authorization: Bearer.")
    return token.strip()


def _unauthenticated(detail: str) -> ApiProblem:
    return ApiProblem(ProblemCode.AUTHENTICATION_REQUIRED, detail, headers=_CHALLENGE)


# Escopos de 1.6 para as rotas de arquivos.
ReadFiles = Annotated[Principal, Depends(require_scope(Scope.FILES_READ))]
WriteFiles = Annotated[Principal, Depends(require_scope(Scope.FILES_WRITE))]
