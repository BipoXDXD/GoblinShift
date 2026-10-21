# Imagem única da aplicação: api, migrate e, a partir da fatia 3, worker e maintenance.
FROM ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 AS uv

FROM python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS build
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

FROM python:3.14.8-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
# Packaging tools are build-only. Remove their bundled dependency copies from runtime.
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.14/ensurepip \
           /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14
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
