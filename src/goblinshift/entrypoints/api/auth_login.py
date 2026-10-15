"""`GET /auth/login/{provider}` e `GET /auth/callback/{provider}` (3.6; ADRs 008, 023 e 025).

Fora de `/api/v1` e do OpenAPI. Rotas públicas: estão na allowlist da matriz de autorização.
O `redirect_uri` sai de `WebIdentityConfig.public_origin`, nunca do header `Host`.

O Authlib cuida do protocolo: Authorization Code com PKCE S256, `state` e, no Google, `nonce` e
a validação do ID token (assinatura RS256 pela JWKS, `iss`, `aud`, `exp`, `nonce`). O `state`
fica em `login_state` e só vale no navegador que tem o marcador no cookie `__Secure-gs_login`.
Toda recusa (provedor, state, token, conta bloqueada ou pendente) responde o mesmo redirect
genérico, sem dizer qual foi o caso; o log do servidor registra só o provedor e o tipo da falha.

As rotas são `async` porque a integração Starlette do Authlib é; o banco é acessado numa thread
do pool, em transação curta, e nunca durante uma chamada ao provedor.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# O Authlib não publica tipos (sem py.typed); o que sai dele é tratado como `Any` e convertido
# logo em tipos do projeto (`ExternalIdentity`).
from authlib.integrations.starlette_client import (  # type: ignore[import-untyped]
    OAuth,
    OAuthError,
)
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from httpx2 import HTTPError, HTTPStatusError
from joserfc.errors import JoseError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from goblinshift.entrypoints.api.cookies import SESSION_COOKIE, set_session_cookies
from goblinshift.entrypoints.api.dependencies import Now
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode
from goblinshift.identity import (
    AuditEventType,
    AuditOutcome,
    ExternalIdentity,
    IssuedSession,
    LoginRefusal,
    authenticate_session,
    record_audit_event,
    resolve_login,
    revoke_session,
    start_session,
)
from goblinshift.identity.login_state import LOGIN_STATE_TTL, PostgresLoginStateCache
from goblinshift.identity.providers import (
    GITHUB_ACCEPT,
    GITHUB_AUTHORIZE_URL,
    GITHUB_TOKEN_URL,
    GITHUB_USER_URL,
    GOOGLE_ACCEPTED_ISSUERS,
    GOOGLE_AUTHORIZE_URL,
    GOOGLE_ID_TOKEN_ALGORITHMS,
    GOOGLE_ISSUER,
    GOOGLE_JWKS_URL,
    GOOGLE_SCOPE,
    GOOGLE_TOKEN_URL,
    ProviderName,
    github_identity,
    google_identity,
)
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import OAuthClient, WebIdentityConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", include_in_schema=False)

# Mesmo destino para toda recusa: a SPA mostra "não foi possível entrar" (sem enumeração).
LOGIN_FAILED_LOCATION = "/?login_error=failed"
# Proteção contra open redirect: só caminho local, sem `//`, `\`, controle nem esquema.
RETURN_TO_PATTERN = re.compile(r"/(?!/)[A-Za-z0-9/_\-.?=&%]*")
MAX_RETURN_TO_CHARS = 512
DEFAULT_RETURN_TO = "/"
REAUTH_PROMPT = "select_account"
# Falhas esperadas de um provedor externo ou de um callback forjado. Qualquer outra exceção é
# defeito nosso e segue para o handler de erro 500.
LOGIN_ERRORS = (OAuthError, JoseError, HTTPError, ValueError)

IdentityReader = Callable[[Any, Request], Awaitable[ExternalIdentity]]


@dataclass(frozen=True)
class LoginProvider:
    """Um provedor registrado: o cliente do Authlib e como ele vira `ExternalIdentity`."""

    oauth_app: Any  # StarletteOAuth2App do Authlib
    read_identity: IdentityReader

    async def fetch_identity(self, request: Request) -> ExternalIdentity:
        return await self.read_identity(self.oauth_app, request)


LoginProviders = dict[ProviderName, LoginProvider]


def install(app: FastAPI, web: WebIdentityConfig) -> None:
    """Registra os provedores ligados na configuração em `app.state`; chamado pelo `create_app`."""
    cache = PostgresLoginStateCache(app.state.session_factory)
    app.state.login_providers = build_login_providers(web, cache)


def build_login_providers(
    web: WebIdentityConfig, cache: PostgresLoginStateCache, *, transport: Any = None
) -> LoginProviders:
    """Clientes do Authlib para os provedores configurados; `transport` existe para os testes."""
    oauth = OAuth(cache=cache)
    http = {"timeout": web.oauth_http_timeout_s}
    if transport is not None:
        http["transport"] = transport
    providers: LoginProviders = {}
    if web.google is not None:
        providers[ProviderName.GOOGLE] = LoginProvider(
            _register_google(oauth, web.google, http), _read_google_identity
        )
    if web.github is not None:
        providers[ProviderName.GITHUB] = LoginProvider(
            _register_github(oauth, web.github, http), _read_github_identity
        )
    return providers


def _register_google(oauth: Any, client: OAuthClient, http: dict[str, Any]) -> Any:
    # Sem `server_metadata_url`: endereços, emissor e algoritmos vêm do código (providers.py).
    app = oauth.register(
        ProviderName.GOOGLE.value,
        client_id=client.client_id,
        client_secret=client.client_secret,
        authorize_url=GOOGLE_AUTHORIZE_URL,
        access_token_url=GOOGLE_TOKEN_URL,
        jwks_uri=GOOGLE_JWKS_URL,
        issuer=GOOGLE_ISSUER,
        id_token_signing_alg_values_supported=list(GOOGLE_ID_TOKEN_ALGORITHMS),
        client_kwargs={"scope": GOOGLE_SCOPE, "code_challenge_method": "S256", **http},
    )
    _limit_state_lifetime(app)
    return app


def _register_github(oauth: Any, client: OAuthClient, http: dict[str, Any]) -> Any:
    # Sem `scope`: o token só lê o perfil público, numa única chamada (ADR 008).
    app = oauth.register(
        ProviderName.GITHUB.value,
        client_id=client.client_id,
        client_secret=client.client_secret,
        authorize_url=GITHUB_AUTHORIZE_URL,
        access_token_url=GITHUB_TOKEN_URL,
        client_kwargs={"code_challenge_method": "S256", **http},
    )
    _limit_state_lifetime(app)
    return app


def _limit_state_lifetime(app: Any) -> None:
    """O marcador no cookie vence junto com o estado no banco (o padrão do Authlib é 1 h)."""
    app.framework.expires_in = int(LOGIN_STATE_TTL.total_seconds())


async def _read_google_identity(oauth_app: Any, request: Request) -> ExternalIdentity:
    token = await oauth_app.authorize_access_token(
        request, claims_options=_google_claims_options(oauth_app.client_id)
    )
    # Sem `id_token` o Authlib não valida nada e não devolve `userinfo`.
    userinfo = token.get("userinfo")
    if userinfo is None:
        raise ValueError("resposta do Google sem ID token")
    return google_identity(userinfo)


def _google_claims_options(client_id: str) -> dict[str, Any]:
    """Claims obrigatórios e valores aceitos; sem `aud` aqui, o Authlib não confere a audiência.

    O `nonce` é conferido pelo Authlib contra o do estado do login; a lista é nova a cada uso
    porque o Authlib altera o dicionário.
    """
    return {
        "iss": {"essential": True, "values": list(GOOGLE_ACCEPTED_ISSUERS)},
        "aud": {"essential": True, "values": [client_id]},
        "sub": {"essential": True},
        "exp": {"essential": True},
        "nonce": {"essential": True},
    }


async def _read_github_identity(oauth_app: Any, request: Request) -> ExternalIdentity:
    token = await oauth_app.authorize_access_token(request)
    # O token só existe nesta função: nunca é gravado nem registrado no log.
    response = await oauth_app.get(GITHUB_USER_URL, token=token, headers={"Accept": GITHUB_ACCEPT})
    response.raise_for_status()
    return github_identity(response.content)


# --- Rotas ------------------------------------------------------------------------------------


@router.get("/login/{provider}")
async def start_login(
    request: Request, provider: str, return_to: str | None = None, reauth: str | None = None
) -> Response:
    """Redireciona ao provedor com PKCE e `state`; só `return_to` e `reauth=1` são aceitos."""
    name = _provider_name(provider)
    login_provider = _providers(request).get(name)
    if login_provider is None:
        return _login_failed()
    request.session[_return_to_key(name)] = _safe_return_to(return_to)
    extra = {"prompt": REAUTH_PROMPT} if reauth == "1" else {}
    response: Response = await login_provider.oauth_app.authorize_redirect(
        request, _redirect_uri(request, name), **extra
    )
    return response


@router.get("/callback/{provider}")
async def finish_login(request: Request, provider: str, now: Now) -> Response:
    """Valida a volta do provedor, resolve a conta e emite uma sessão nova."""
    name = _provider_name(provider)
    factory: sessionmaker[Session] = request.app.state.session_factory
    ip = request.client.host if request.client else None
    return_to = _safe_return_to(request.session.pop(_return_to_key(name), None))
    login_provider = _providers(request).get(name)
    if login_provider is None:
        await run_in_threadpool(_record_login_failure, factory, ip, now)
        return _login_failed()
    try:
        identity = await login_provider.fetch_identity(request)
    except LOGIN_ERRORS as exc:
        logger.warning("login %s recusado: %s", name, _failure_kind(exc))
        await run_in_threadpool(_record_login_failure, factory, ip, now)
        return _login_failed()

    web: WebIdentityConfig = request.app.state.web_identity
    result = await run_in_threadpool(
        _complete_login,
        factory,
        identity,
        web.signup_open,
        web.csrf_key,
        request.cookies.get(SESSION_COOKIE),
        ip,
        now,
    )
    if isinstance(result, LoginRefusal):
        logger.info("login %s recusado: conta %s", name, result.reason.name)
        return _login_failed()
    response = RedirectResponse(return_to, status_code=303)
    set_session_cookies(response, result)
    return response


# --- Banco (numa thread do pool) --------------------------------------------------------------


def _complete_login(
    factory: sessionmaker[Session],
    identity: ExternalIdentity,
    signup_open: bool,
    csrf_key: bytes,
    previous_token: str | None,
    ip: str | None,
    now: datetime,
) -> IssuedSession | LoginRefusal:
    """Conta, sessão nova e auditoria numa transação só (a conta pendente também é gravada)."""
    with unit_of_work(factory) as session:
        resolved = resolve_login(session, identity, signup_open=signup_open, now=now)
        if isinstance(resolved, LoginRefusal):
            _record(session, AuditEventType.LOGIN_FAILED, None, ip, AuditOutcome.FAILURE, now)
            return resolved
        _revoke_previous_session(session, previous_token, now)
        issued = start_session(
            session, user_id=resolved, authenticated_at=now, now=now, csrf_key=csrf_key
        )
        _record(session, AuditEventType.LOGIN_SUCCEEDED, resolved, ip, AuditOutcome.SUCCESS, now)
        return issued


def _revoke_previous_session(session: Session, token: str | None, now: datetime) -> None:
    """Fixação de sessão: o cookie recebido nunca é reaproveitado, e a sessão dele termina."""
    if token is None:
        return
    previous = authenticate_session(session, token, now)
    if previous is not None and previous.session_id is not None:
        revoke_session(session, previous.session_id, now)


def _record_login_failure(factory: sessionmaker[Session], ip: str | None, now: datetime) -> None:
    with unit_of_work(factory) as session:
        _record(session, AuditEventType.LOGIN_FAILED, None, ip, AuditOutcome.FAILURE, now)


def _record(
    session: Session,
    event: AuditEventType,
    user_id: UUID | None,
    ip: str | None,
    outcome: AuditOutcome,
    now: datetime,
) -> None:
    record_audit_event(session, event=event, user_id=user_id, ip=ip, outcome=outcome, now=now)


# --- Apoio ------------------------------------------------------------------------------------


def _provider_name(provider: str) -> ProviderName:
    try:
        return ProviderName(provider)
    except ValueError:
        raise ApiProblem(ProblemCode.NOT_FOUND, "Provedor de login desconhecido.") from None


def _providers(request: Request) -> LoginProviders:
    providers: LoginProviders = request.app.state.login_providers
    return providers


def _redirect_uri(request: Request, name: ProviderName) -> str:
    web: WebIdentityConfig = request.app.state.web_identity
    return f"{web.public_origin}/auth/callback/{name.value}"


def _return_to_key(name: ProviderName) -> str:
    return f"return_to_{name.value}"


def _safe_return_to(value: object) -> str:
    if (
        isinstance(value, str)
        and len(value) <= MAX_RETURN_TO_CHARS
        and RETURN_TO_PATTERN.fullmatch(value)
    ):
        return value
    return DEFAULT_RETURN_TO


def _failure_kind(exc: Exception) -> str:
    """Tipo da falha para o log, sem a mensagem: ela pode trazer dado do provedor ou do cliente."""
    if isinstance(exc, HTTPStatusError):
        return f"{type(exc).__name__} {exc.response.status_code}"
    return type(exc).__name__


def _login_failed() -> Response:
    return RedirectResponse(LOGIN_FAILED_LOCATION, status_code=303)
