"""`GET /auth/login/{provider}` e `GET /auth/callback/{provider}` (3.6; ADRs 008, 023 e 025).

Fora de `/api/v1` e do OpenAPI. Rotas públicas: estão na allowlist da matriz de autorização.
O `redirect_uri` sai de `WebIdentityConfig.public_origin`, nunca do header `Host`.

O Authlib cuida do protocolo: Authorization Code com PKCE S256, `state` e, no Google, `nonce` e
a validação do ID token (assinatura RS256 pela JWKS, `iss`, `aud`, `exp`, `nonce`). O `state`
fica em `login_state` e só vale no navegador que tem o marcador no cookie `__Host-gs_login`.
Toda recusa (provedor, state, token, conta bloqueada ou pendente) responde o mesmo redirect
genérico, sem dizer qual foi o caso; o log do servidor registra só o provedor e o tipo da falha.
O redirect leva no fragmento um código de referência, o `request_id` do callback (o mesmo do
header `X-Request-ID`), gravado no evento `LOGIN_FAILED` com a conta recusada: a pessoa o cita no
recurso contra o bloqueio e o operador acha a conta (J-10). O código é aleatório e igual em forma
para toda recusa; no fragmento, o navegador não o envia a servidor nenhum, nem no `Referer`.

Reautenticação (`reauth=1`): o início guarda, no cookie de login, a conta da sessão que o
navegador já tem, e o callback só aceita a identidade dessa mesma conta. Outra conta (ou outro
provedor) é recusada antes de qualquer escrita: a sessão anterior continua valendo e nenhuma
sessão recente nasce para quem não pediu a confirmação. Sem sessão válida, é um login comum.

Recência (ADR 065): o Google não força a senha (não aceita `prompt=login` nem `max_age`), então
todo pedido a ele exige o claim `auth_time`, e a sessão guarda `min(auth_time, agora)` como
`authenticated_at`. Um navegador logado no Google há dias entra, mas não abre `DELETE /me` nem a
emissão de chave. Sem `auth_time` ("Session age claims" desligado), vale a hora do callback, com o
alerta `google_auth_time_missing`. O GitHub não informa a hora: o login dele conta como recente,
risco aceito no mesmo ADR. Uma reautenticação da mesma conta com `auth_time` fora da janela de
10 min é recusada com aviso próprio (`/?login_error=reauth_stale`): a sessão atual continua,
nenhuma outra nasce, e a auditoria grava `REAUTH_REQUIRED`. Só chega aqui quem tem a sessão e a
mesma identidade, então o aviso não enumera conta.

As rotas são `async` porque a integração Starlette do Authlib é; o banco é acessado numa thread
do pool, em transação curta, e nunca durante uma chamada ao provedor.

O início sem autenticação grava um estado em `login_state`. Com `MAX_LIVE_LOGIN_STATES` vivos, o
início novo recebe a mesma recusa genérica, sem gravar estado nem auditoria, e o log avisa no
máximo uma vez por minuto (`alert=login_state_cap`); o estado de quem já está no provedor fica.
A recusa sem conta grava `LOGIN_FAILED` até `MAX_ANONYMOUS_LOGIN_FAILURES_PER_MINUTE` por minuto;
acima disso fica só no log (`alert=login_failure_audit_cap`, também uma vez por minuto). Provedor
desligado é erro de configuração e não grava nada.
"""

import asyncio
import logging
import re
import time
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
from httpx2 import AsyncBaseTransport, AsyncHTTPTransport, HTTPError, HTTPStatusError
from joserfc.errors import JoseError
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from goblinshift.entrypoints.api.cookies import SESSION_COOKIE, set_session_cookies
from goblinshift.entrypoints.api.dependencies import Now
from goblinshift.entrypoints.api.oauth_http import MAX_PROVIDER_RESPONSE_BYTES, CappedTransport
from goblinshift.entrypoints.api.problems import ApiProblem, ProblemCode, request_id_of
from goblinshift.identity import (
    MAX_ANONYMOUS_LOGIN_FAILURES_PER_MINUTE,
    RECENT_AUTH_WINDOW,
    AuditEventType,
    AuditOutcome,
    ExternalIdentity,
    IssuedSession,
    LoginRefusal,
    LoginRefused,
    Principal,
    authenticate_session,
    find_login_account,
    record_anonymous_login_failure,
    record_audit_event,
    resolve_login,
    resolve_session,
    revoke_session,
    start_session,
)
from goblinshift.identity.login_state import (
    LOGIN_STATE_TTL,
    MAX_LIVE_LOGIN_STATES,
    PostgresLoginStateCache,
    login_states_full,
)
from goblinshift.identity.providers import (
    GITHUB_ACCEPT,
    GITHUB_AUTHORIZE_URL,
    GITHUB_TOKEN_URL,
    GITHUB_USER_URL,
    GOOGLE_ACCEPTED_ISSUERS,
    GOOGLE_AUTH_TIME_CLAIMS,
    GOOGLE_AUTHORIZE_URL,
    GOOGLE_ID_TOKEN_ALGORITHMS,
    GOOGLE_ISSUER,
    GOOGLE_JWKS_URL,
    GOOGLE_SCOPE,
    GOOGLE_TOKEN_URL,
    MalformedProviderResponseError,
    ProviderName,
    github_identity,
    google_identity,
)
from goblinshift.platform.db import unit_of_work
from goblinshift.platform.settings import OAuthClient, WebIdentityConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", include_in_schema=False)

# Mesmo destino para toda recusa: a SPA mostra "não foi possível entrar" (sem enumeração) e o
# código de referência do fragmento (`#ref=<request_id>`).
LOGIN_FAILED_LOCATION = "/?login_error=failed"
LOGIN_REF_FRAGMENT = "ref"
# Reautenticação sem senha no Google: a SPA explica como sair da conta Google e entrar de novo.
LOGIN_REAUTH_STALE_LOCATION = "/?login_error=reauth_stale"
# Proteção contra open redirect: só caminho local, sem `//`, `\`, controle nem esquema.
RETURN_TO_PATTERN = re.compile(r"/(?!/)[A-Za-z0-9/_\-.?=&%]*")
MAX_RETURN_TO_CHARS = 512
DEFAULT_RETURN_TO = "/"
REAUTH_PROMPT = "select_account"
# Login no Google sem `auth_time`: a recência cai na hora do callback (D3 do plano login-e-borda).
GOOGLE_AUTH_TIME_MISSING_ALERT = "google_auth_time_missing"
# Falhas esperadas de um provedor externo ou de um callback forjado. Qualquer outra exceção é
# defeito nosso e segue para o handler de erro 500. `TimeoutError` é o prazo total;
# `MalformedProviderResponseError`, o JSON válido com forma errada (`_consume_provider`).
LOGIN_ERRORS = (
    OAuthError,
    JoseError,
    HTTPError,
    ValueError,
    TimeoutError,
    MalformedProviderResponseError,
)
# O que o Authlib levanta ao ler um token ou uma JWKS com forma errada (a JWKS em lista ou `null`,
# o `token_type` numérico).
_SHAPE_ERRORS = (KeyError, AttributeError, TypeError)
# Prazo do callback inteiro (token, JWKS, `/user`) em múltiplos de `oauth_http_timeout_s`, que o
# httpx aplica a cada fase de cada chamada.
LOGIN_FETCH_TIMEOUT_FACTOR = 2
# Avisos de teto do login anônimo: no máximo um por intervalo, por processo.
CAP_WARNING_INTERVAL_S = 60.0

IdentityReader = Callable[[Any, Request], Awaitable[ExternalIdentity]]


@dataclass(frozen=True)
class LoginProvider:
    """Um provedor registrado: o cliente do Authlib e como ele vira `ExternalIdentity`."""

    oauth_app: Any  # StarletteOAuth2App do Authlib
    read_identity: IdentityReader

    async def fetch_identity(self, request: Request, deadline_s: float) -> ExternalIdentity:
        """Token, JWKS e `/user` dentro de um prazo só: o do httpx vale por fase de cada chamada,
        e um provedor que pinga bytes nunca estoura nenhuma delas."""
        async with asyncio.timeout(deadline_s):
            return await self.read_identity(self.oauth_app, request)


LoginProviders = dict[ProviderName, LoginProvider]


class WarningThrottle:
    """Libera um aviso no máximo uma vez por `interval_s` (relógio monotônico de quem chama).

    Numa enxurrada, cada recusa cairia no log; basta uma linha por intervalo para o alerta acender.
    Usado só no laço de eventos, sem concorrência entre threads.
    """

    def __init__(self, interval_s: float) -> None:
        self._interval_s = interval_s
        self._last: float | None = None

    def ready(self, monotonic_now: float) -> bool:
        if self._last is not None and monotonic_now - self._last < self._interval_s:
            return False
        self._last = monotonic_now
        return True


def install(app: FastAPI, web: WebIdentityConfig) -> None:
    """Registra os provedores ligados na configuração em `app.state`; chamado pelo `create_app`.

    O pool HTTP dos provedores fica em `app.state.login_transport`, e o lifespan o fecha na parada.
    """
    cache = PostgresLoginStateCache(app.state.session_factory)
    app.state.login_transport = AsyncHTTPTransport()
    app.state.login_providers = build_login_providers(
        web, cache, transport=app.state.login_transport
    )
    app.state.login_cap_warnings = WarningThrottle(CAP_WARNING_INTERVAL_S)
    app.state.login_failure_cap_warnings = WarningThrottle(CAP_WARNING_INTERVAL_S)


def build_login_providers(
    web: WebIdentityConfig, cache: PostgresLoginStateCache, *, transport: AsyncBaseTransport
) -> LoginProviders:
    """Clientes do Authlib para os provedores configurados, todos sobre `transport` com teto."""
    oauth = OAuth(cache=cache)
    http = {
        "timeout": web.oauth_http_timeout_s,
        "transport": CappedTransport(transport, MAX_PROVIDER_RESPONSE_BYTES),
    }
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


async def _consume_provider[T](call: Awaitable[T]) -> T:
    """Uma chamada do Authlib que lê a resposta do provedor; só ela fica neste `try`.

    O erro de forma vira erro de domínio aqui, na fronteira: o mesmo tipo de erro no resto do
    callback é defeito nosso e segue para o 500.
    """
    try:
        return await call
    except _SHAPE_ERRORS as exc:
        raise MalformedProviderResponseError(type(exc).__name__) from exc


async def _read_google_identity(oauth_app: Any, request: Request) -> ExternalIdentity:
    token = await _consume_provider(
        oauth_app.authorize_access_token(
            request, claims_options=_google_claims_options(oauth_app.client_id)
        )
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
    token = await _consume_provider(oauth_app.authorize_access_token(request))
    # O token só existe nesta função: nunca é gravado nem registrado no log. O Authlib lê o
    # `token_type` dele ao montar o pedido, então a chamada também consome dado do provedor.
    response = await _consume_provider(
        oauth_app.get(GITHUB_USER_URL, token=token, headers={"Accept": GITHUB_ACCEPT})
    )
    response.raise_for_status()
    return github_identity(response.content)


# --- Rotas ------------------------------------------------------------------------------------


@router.get("/login/{provider}")
async def start_login(
    request: Request,
    provider: str,
    now: Now,
    return_to: str | None = None,
    reauth: str | None = None,
) -> Response:
    """Redireciona ao provedor com PKCE e `state`; só `return_to` e `reauth=1` são aceitos."""
    name = _provider_name(provider)
    factory: sessionmaker[Session] = request.app.state.session_factory
    login_provider = _providers(request).get(name)
    if login_provider is None:
        logger.info("login %s: provedor desligado", name)
        return _login_failed(_attempt(request, now))
    if await run_in_threadpool(_login_states_full, factory, now):
        _warn_login_state_cap(request)
        return _login_failed(_attempt(request, now))
    request.session[_return_to_key(name)] = _safe_return_to(return_to)
    reauthenticating = reauth == "1"
    reauth_user = None
    if reauthenticating:
        reauth_user = await run_in_threadpool(
            _session_owner, factory, request.cookies.get(SESSION_COOKIE), now
        )
    if reauth_user is None:
        request.session.pop(_reauth_key(name), None)
    else:
        request.session[_reauth_key(name)] = str(reauth_user)
    extra = {"prompt": REAUTH_PROMPT} if reauthenticating else {}
    if name is ProviderName.GOOGLE:
        extra["claims"] = GOOGLE_AUTH_TIME_CLAIMS
    response: Response = await login_provider.oauth_app.authorize_redirect(
        request, _redirect_uri(request, name), **extra
    )
    return response


@router.get("/callback/{provider}")
async def finish_login(request: Request, provider: str, now: Now) -> Response:
    """Valida a volta do provedor, resolve a conta e emite uma sessão nova."""
    name = _provider_name(provider)
    factory: sessionmaker[Session] = request.app.state.session_factory
    attempt = _attempt(request, now)
    return_to = _safe_return_to(request.session.pop(_return_to_key(name), None))
    reauth_user = _parse_reauth_user(request.session.pop(_reauth_key(name), None))
    login_provider = _providers(request).get(name)
    if login_provider is None:
        logger.info("login %s: provedor desligado", name)
        return _login_failed(attempt)
    web: WebIdentityConfig = request.app.state.web_identity
    try:
        identity = await login_provider.fetch_identity(
            request, LOGIN_FETCH_TIMEOUT_FACTOR * web.oauth_http_timeout_s
        )
    except LOGIN_ERRORS as exc:
        logger.warning("login %s recusado: %s", name, _failure_kind(exc))
        if not await run_in_threadpool(_record_login_failure, factory, attempt):
            _warn_login_failure_audit_cap(request)
        return _login_failed(attempt)
    if name is ProviderName.GOOGLE and identity.authenticated_at is None:
        logger.warning(
            "login google sem auth_time",
            extra={"fields": {"alert": GOOGLE_AUTH_TIME_MISSING_ALERT}},
        )

    result = await run_in_threadpool(
        _complete_login,
        factory,
        identity,
        web.signup_open,
        web.csrf_key,
        request.cookies.get(SESSION_COOKIE),
        attempt,
        reauth_user,
    )
    if isinstance(result, LoginRefusal):
        logger.info("login %s recusado: conta %s", name, result.reason.name)
        if result.reason is LoginRefused.REAUTH_STALE:
            return _reauth_stale(attempt)
        return _login_failed(attempt)
    response = RedirectResponse(return_to, status_code=303)
    set_session_cookies(response, result)
    return response


# --- Banco (numa thread do pool) --------------------------------------------------------------


@dataclass(frozen=True)
class LoginAttempt:
    """O que a auditoria guarda de cada callback: IP (truncado na gravação), código e hora."""

    ip: str | None
    request_id: UUID
    now: datetime


def _complete_login(
    factory: sessionmaker[Session],
    identity: ExternalIdentity,
    signup_open: bool,
    csrf_key: bytes,
    previous_token: str | None,
    attempt: LoginAttempt,
    reauth_user: UUID | None,
) -> IssuedSession | LoginRefusal:
    """Conta, sessão nova e auditoria numa transação só (a conta pendente também é gravada).

    Com `reauth_user`, a identidade precisa ser a dessa conta, conferida antes de qualquer
    escrita: senão a recusa é auditada na conta da sessão, e nada mais muda. A mesma conta com o
    login no provedor fora da janela de recência (o Google confirmou sem senha) também é recusada
    sem escrita além de `REAUTH_REQUIRED`: a sessão nova não seria recente, e a confirmação
    entraria em laço sem explicação.
    """
    now = attempt.now
    with unit_of_work(factory) as session:
        if reauth_user is not None:
            if find_login_account(session, identity) != reauth_user:
                _record(session, AuditEventType.LOGIN_FAILED, reauth_user, attempt)
                return LoginRefusal(reauth_user, LoginRefused.REAUTH_MISMATCH)
            if now - _session_authenticated_at(identity, now) > RECENT_AUTH_WINDOW:
                _record(session, AuditEventType.REAUTH_REQUIRED, reauth_user, attempt)
                return LoginRefusal(reauth_user, LoginRefused.REAUTH_STALE)
        resolved = resolve_login(session, identity, signup_open=signup_open, now=now)
        if isinstance(resolved, LoginRefusal):
            _record(session, AuditEventType.LOGIN_FAILED, resolved.account_id, attempt)
            return resolved
        _revoke_previous_session(session, previous_token, now)
        issued = start_session(
            session,
            user_id=resolved,
            authenticated_at=_session_authenticated_at(identity, now),
            now=now,
            csrf_key=csrf_key,
        )
        _record(session, AuditEventType.LOGIN_SUCCEEDED, resolved, attempt)
        return issued


def _session_authenticated_at(identity: ExternalIdentity, now: datetime) -> datetime:
    """A hora do login no provedor para a recência: a que ele informou, nunca depois de `now`.

    Sem a hora (GitHub, ou Google sem `auth_time`), vale `now`. Um `auth_time` no futuro (relógio
    do provedor adiantado) também vira `now`, sem recusar o login.
    """
    if identity.authenticated_at is None:
        return now
    return min(identity.authenticated_at, now)


def _revoke_previous_session(session: Session, token: str | None, now: datetime) -> None:
    """Fixação de sessão: o cookie recebido nunca é reaproveitado, e a sessão dele termina."""
    if token is None:
        return
    previous = authenticate_session(session, token, now)
    if previous is not None and previous.session_id is not None:
        revoke_session(session, previous.session_id, now)


def _session_owner(factory: sessionmaker[Session], token: str | None, now: datetime) -> UUID | None:
    """Dono da sessão válida do cookie, sem registrar o uso (é só o início do login)."""
    if token is None:
        return None
    with unit_of_work(factory) as session:
        resolved = resolve_session(session, token, now)
    return resolved.owner_id if isinstance(resolved, Principal) else None


def _login_states_full(factory: sessionmaker[Session], now: datetime) -> bool:
    # O teto é lido aqui, e não no import, para o teste trocá-lo com `monkeypatch`.
    with unit_of_work(factory) as session:
        return login_states_full(session, now, MAX_LIVE_LOGIN_STATES)


def _record_login_failure(factory: sessionmaker[Session], attempt: LoginAttempt) -> bool:
    """`LOGIN_FAILED` sem conta, dentro do teto por minuto; devolve se gravou."""
    with unit_of_work(factory) as session:
        return record_anonymous_login_failure(
            session,
            ip=attempt.ip,
            request_id=attempt.request_id,
            now=attempt.now,
            # Lido aqui, e não no import, para o teste trocá-lo com `monkeypatch`.
            limit=MAX_ANONYMOUS_LOGIN_FAILURES_PER_MINUTE,
        )


def _record(
    session: Session, event: AuditEventType, user_id: UUID | None, attempt: LoginAttempt
) -> None:
    outcome = (
        AuditOutcome.SUCCESS if event is AuditEventType.LOGIN_SUCCEEDED else AuditOutcome.FAILURE
    )
    record_audit_event(
        session,
        event=event,
        user_id=user_id,
        ip=attempt.ip,
        outcome=outcome,
        now=attempt.now,
        request_id=attempt.request_id,
    )


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


def _reauth_key(name: ProviderName) -> str:
    return f"reauth_{name.value}"


def _parse_reauth_user(value: object) -> UUID | None:
    """A conta guardada no início; o cookie é assinado, mas o valor é conferido mesmo assim."""
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _safe_return_to(value: object) -> str:
    if (
        isinstance(value, str)
        and len(value) <= MAX_RETURN_TO_CHARS
        and RETURN_TO_PATTERN.fullmatch(value)
    ):
        return value
    return DEFAULT_RETURN_TO


def _attempt(request: Request, now: datetime) -> LoginAttempt:
    return LoginAttempt(
        ip=request.client.host if request.client else None,
        request_id=UUID(request_id_of(request)),
        now=now,
    )


def _failure_kind(exc: Exception) -> str:
    """Tipo da falha para o log, sem a mensagem: ela pode trazer dado do provedor ou do cliente."""
    if isinstance(exc, HTTPStatusError):
        return f"{type(exc).__name__} {exc.response.status_code}"
    return type(exc).__name__


def _reauth_stale(attempt: LoginAttempt) -> Response:
    """O aviso da reautenticação sem senha, com o mesmo código de referência no fragmento."""
    location = f"{LOGIN_REAUTH_STALE_LOCATION}#{LOGIN_REF_FRAGMENT}={attempt.request_id}"
    return RedirectResponse(location, status_code=303)


def _warn_login_state_cap(request: Request) -> None:
    throttle: WarningThrottle = request.app.state.login_cap_warnings
    if throttle.ready(time.monotonic()):
        logger.warning(
            "login: teto de estados vivos, início recusado",
            extra={"fields": {"alert": "login_state_cap"}},
        )


def _warn_login_failure_audit_cap(request: Request) -> None:
    # O código de referência desta recusa não leva a evento nenhum: o `request_id` fica no log.
    throttle: WarningThrottle = request.app.state.login_failure_cap_warnings
    if throttle.ready(time.monotonic()):
        logger.warning(
            "login: teto da auditoria de falha sem conta, evento só no log",
            extra={"fields": {"alert": "login_failure_audit_cap"}},
        )


def _login_failed(attempt: LoginAttempt) -> Response:
    """O redirect genérico, com o código de referência no fragmento (nunca na query)."""
    location = f"{LOGIN_FAILED_LOCATION}#{LOGIN_REF_FRAGMENT}={attempt.request_id}"
    return RedirectResponse(location, status_code=303)
