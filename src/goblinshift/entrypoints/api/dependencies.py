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

from goblinshift.entrypoints.api.cookies import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.entrypoints.api.wide_event import record_principal
from goblinshift.files.storage import FileStorage
from goblinshift.identity import (
    RECENT_AUTH_WINDOW,
    AuditEventType,
    AuditOutcome,
    Principal,
    Scope,
    authenticate_api_key,
    authenticate_session,
    cross_site_request,
    csrf_token_matches,
    record_audit_event,
)
from goblinshift.platform.settings import WebIdentityConfig

BEARER_SCHEME = "bearer"
# Métodos que nunca alteram estado (3.6): dispensam o CSRF.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
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
    """Cookie de sessão **ou** `Authorization: Bearer <chave>` → Principal; senão 401.

    Os dois juntos são ambíguos → 400. Mutação por cookie passa pelo CSRF (403 `CSRF_FAILED`);
    a chave não usa cookie e dispensa o CSRF. Mesma resposta para credencial malformada,
    desconhecida, expirada, revogada ou de conta bloqueada: o cliente não aprende qual foi o
    caso, o log do servidor registra.
    """
    _reject_simultaneous_credentials(request)
    if SESSION_COOKIE in request.cookies:
        principal = _session_from_cookie(request, session, now)
        if principal is None:
            raise _unauthenticated("A sessão expirou ou foi encerrada; entre de novo.")
        return principal
    token = _bearer_token(request)
    with session.begin():
        principal = authenticate_api_key(session, token, now)
    if principal is None:
        raise _unauthenticated("A chave de API é inválida, expirou ou foi revogada.")
    record_principal(principal.owner_id, principal.via)
    return principal


CurrentPrincipal = Annotated[Principal, Depends(authenticate)]


def optional_session(request: Request, session: DbSession, now: Now) -> Principal | None:
    """Sessão do cookie, ou None sem sessão válida (`POST /auth/logout`).

    Com sessão válida, a mutação passa pelo CSRF como em `authenticate`: sem isso, outro site
    derrubaria a sessão do usuário.
    """
    _reject_simultaneous_credentials(request)
    return _session_from_cookie(request, session, now)


OptionalSession = Annotated[Principal | None, Depends(optional_session)]


def require_scope(scope: Scope) -> Callable[[Principal], Principal]:
    """Dependência que autentica e exige `scope`; sem ele → 403 `INSUFFICIENT_SCOPE`."""

    def check_scope(principal: CurrentPrincipal) -> Principal:
        if scope not in principal.scopes:
            raise ApiProblem(
                ProblemCode.INSUFFICIENT_SCOPE, f"A chave não tem o escopo {scope.value}."
            )
        return principal

    return check_scope


def require_session(
    principal: CurrentPrincipal, request: Request, session: DbSession, now: Now
) -> Principal:
    """Só sessão web (`/api-keys`, `DELETE /me`, `logout-all`).

    Chave → 403 `FORBIDDEN`, auditado como negação de autorização.
    """
    if principal.via != "session":
        _audit_failure(session, AuditEventType.ACCESS_DENIED, principal, request, now)
        raise ApiProblem(ProblemCode.FORBIDDEN, "Esta operação exige a sessão web, não uma chave.")
    return principal


SessionPrincipal = Annotated[Principal, Depends(require_session)]


def require_recent_session(
    principal: SessionPrincipal, request: Request, session: DbSession, now: Now
) -> Principal:
    """Sessão com login no provedor há no máximo `RECENT_AUTH_WINDOW` (10 min, fronteira
    inclusiva); senão 401 `REAUTH_REQUIRED`, auditado.
    """
    if principal.authenticated_at is None or now - principal.authenticated_at > RECENT_AUTH_WINDOW:
        _audit_failure(session, AuditEventType.REAUTH_REQUIRED, principal, request, now)
        raise ApiProblem(
            ProblemCode.REAUTH_REQUIRED, "Entre de novo no provedor para concluir esta operação."
        )
    return principal


RecentSessionPrincipal = Annotated[Principal, Depends(require_recent_session)]


def client_ip(request: Request) -> str | None:
    """IP do cliente, que o uvicorn resolve do `X-Forwarded-For` só vindo da borda confiável."""
    return request.client.host if request.client is not None else None


def _reject_simultaneous_credentials(request: Request) -> None:
    if SESSION_COOKIE in request.cookies and request.headers.getlist("authorization"):
        raise ApiProblem(
            ProblemCode.BAD_REQUEST,
            "Envie a sessão ou a chave de API, não as duas: credenciais simultâneas são ambíguas.",
        )


def _session_from_cookie(request: Request, session: Session, now: datetime) -> Principal | None:
    token = request.cookies.get(SESSION_COOKIE)
    if token is None:
        return None
    with session.begin():
        principal = authenticate_session(session, token, now)
    if principal is not None and request.method not in SAFE_METHODS:
        _verify_csrf(request, session, principal, now)
    return principal


def _verify_csrf(request: Request, session: Session, principal: Principal, now: datetime) -> None:
    web_identity: WebIdentityConfig = request.app.state.web_identity
    if principal.session_id is None:
        raise RuntimeError("Principal de sessão sem session_id")
    cross_site = cross_site_request(
        request.headers.get("sec-fetch-site"),
        request.headers.get("origin"),
        web_identity.public_origin,
    )
    token_matches = csrf_token_matches(
        web_identity.csrf_key,
        principal.session_id,
        request.cookies.get(CSRF_COOKIE),
        request.headers.get(CSRF_HEADER),
    )
    if cross_site or not token_matches:
        _audit_failure(session, AuditEventType.CSRF_FAILED, principal, request, now)
        raise ApiProblem(
            ProblemCode.CSRF_FAILED,
            f"Mutação pela sessão exige o header {CSRF_HEADER} com o valor do cookie "
            f"{CSRF_COOKIE}, enviado pela própria origem.",
        )


def _audit_failure(
    session: Session,
    event: AuditEventType,
    principal: Principal,
    request: Request,
    now: datetime,
) -> None:
    # Transação própria: a recusa interrompe a requisição, e o evento precisa ficar gravado.
    with session.begin():
        record_audit_event(
            session,
            event=event,
            user_id=principal.owner_id,
            ip=client_ip(request),
            outcome=AuditOutcome.FAILURE,
            now=now,
        )


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
