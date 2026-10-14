"""Fábrica da aplicação FastAPI.

Executada com `uvicorn goblinshift.entrypoints.api.app:create_app --factory`.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from opentelemetry.sdk.trace.export import SpanExporter
from starlette.middleware.sessions import SessionMiddleware

from goblinshift.entrypoints.api import (
    api_keys,
    artifacts,
    auth_login,
    auth_session,
    catalog,
    health,
    me,
    me_delete,
    runs,
    runs_cancel,
    uploads,
)
from goblinshift.entrypoints.api.cookies import (
    LOGIN_COOKIE,
    LOGIN_COOKIE_MAX_AGE_S,
    LOGIN_COOKIE_PATH,
)
from goblinshift.entrypoints.api.problems import PROBLEM_RESPONSES, install_problem_handlers
from goblinshift.entrypoints.api.security_headers import SecurityHeadersMiddleware
from goblinshift.entrypoints.api.transfers import TransferTimeouts
from goblinshift.entrypoints.api.wide_event import WideEventMiddleware, count_queries
from goblinshift.files.storage import FileStorage
from goblinshift.platform.db import make_engine, make_session_factory
from goblinshift.platform.schema import read_code_revisions
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import UserHasher, configure_logging, configure_tracing

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None, *, span_exporter: SpanExporter | None = None
) -> FastAPI:
    """`span_exporter` substitui o exportador OTLP nos testes do evento amplo."""
    settings = settings or Settings()
    # Lidos já na subida: em produção, sem os segredos ou sem o executor-ctl e a imagem do
    # executor, a API não sobe (em vez de falhar no primeiro pedido).
    settings.cursor_signing_key()
    user_hasher = UserHasher(settings.user_hash_key())
    executor_image = settings.executor_image_for_runs()
    web_identity = settings.web_identity()
    configure_logging()
    engine = make_engine(settings)
    code_heads, known_revisions = read_code_revisions(str(settings.alembic_ini))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        yield
        engine.dispose()

    app = FastAPI(
        title="GoblinShift",
        version=settings.service_version,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
        # Uma documentação só, sem o redirect OAuth do Swagger, que nada usa: cada rota pública
        # a mais entra na allowlist da matriz de autorização.
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
    )
    app.state.settings = settings
    app.state.executor_image = executor_image
    app.state.web_identity = web_identity
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.code_heads = code_heads
    app.state.known_revisions = known_revisions
    app.state.storage = FileStorage(settings.storage_root)
    app.state.transfer_timeouts = TransferTimeouts()

    install_problem_handlers(app)
    # Cookie de login (ADR 025): só o marcador do estado do Authlib, cujo conteúdo fica em
    # `login_state`. Middleware ASGI puro, que não toca respostas fora de `/auth`.
    app.add_middleware(
        SessionMiddleware,
        secret_key=web_identity.login_cookie_key.decode(),
        session_cookie=LOGIN_COOKIE,
        max_age=LOGIN_COOKIE_MAX_AGE_S,
        path=LOGIN_COOKIE_PATH,
        same_site="lax",
        https_only=True,
    )
    # Por fora da sessão de login: os cabeçalhos valem também para as respostas de erro. O HSTS
    # fica só no Nginx, que o manda em toda resposta do domínio, inclusive as da SPA.
    app.add_middleware(SecurityHeadersMiddleware, hsts=False)
    # Depois dos handlers e dos demais middlewares: o último acrescentado fica por fora e vê o
    # status final, inclusive o 500 dos handlers.
    app.add_middleware(WideEventMiddleware, hasher=user_hasher)
    count_queries(engine)
    auth_login.install(app, web_identity)

    app.include_router(health.router)
    app.include_router(auth_login.router)
    app.include_router(auth_session.router)
    api = APIRouter(prefix=API_PREFIX, responses=PROBLEM_RESPONSES)
    api.include_router(catalog.router)
    api.include_router(me.router)
    api.include_router(me_delete.router)
    api.include_router(api_keys.router)
    api.include_router(uploads.router)
    api.include_router(artifacts.router)
    api.include_router(runs.router)
    api.include_router(runs_cancel.router)
    app.include_router(api)

    configure_tracing(app, engine, settings, span_exporter)
    return app
