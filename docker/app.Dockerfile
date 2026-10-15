# Imagem única da aplicação: api, migrate e, a partir da fatia 3, worker e maintenance.
FROM ghcr.io/astral-sh/uv:0.12.15 AS uv

FROM python:3.14.8-slim-trixie AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
# license-files do pyproject.toml: o build do projeto recusa metadados sem eles.
COPY LICENSE NOTICE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.14.8-slim-trixie
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
 # Volumes novos herdam o dono destas pastas; sem isso nasceriam do root.
 && install -d -o app -g app -m 700 /var/lib/goblinshift/storage /var/lib/goblinshift/work
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
ARG GS_SERVICE_VERSION=dev
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GS_SERVICE_VERSION=${GS_SERVICE_VERSION}
USER app
EXPOSE 8000
# Sem access log: o evento amplo `main` já registra cada requisição, sem IP completo (plano, 3.7).
CMD ["uvicorn", "goblinshift.entrypoints.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header", \
     "--no-access-log"]
