"""Fábrica da aplicação FastAPI.

Executada com `uvicorn goblinshift.entrypoints.api.app:create_app --factory`.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from goblinshift.entrypoints.api import health
from goblinshift.platform.db import make_engine
from goblinshift.platform.schema import read_code_revisions
from goblinshift.platform.settings import Settings
from goblinshift.platform.telemetry import configure_logging, configure_tracing


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
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
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.code_heads = code_heads
    app.state.known_revisions = known_revisions

    app.include_router(health.router)
    # As rotas do contrato (1.6) entram aqui a partir da fatia 3.
    app.include_router(APIRouter(prefix="/api/v1"))

    configure_tracing(app, engine, settings)
    return app
