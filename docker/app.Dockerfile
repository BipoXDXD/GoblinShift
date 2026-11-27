# Imagem única da aplicação: api, migrate e, a partir da fatia 3, worker e maintenance.
FROM ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 AS uv

FROM python:3.14.10-slim-trixie@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966 AS build
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

FROM python:3.14.10-slim-trixie@sha256:930e1abba7c4e8f96ed93e8012c35d174d7732752731bbd5e8e757d7ec6f3966
# Sobe apt, zlib e libzstd até a base oficial trazer as correções da CVE-2026-109518 (apt 3.0.4),
# das CVE-2026-76844 e CVE-2026-85091 (zlib1g deb13u1) e da CVE-2026-124851 (libzstd1 deb13u1),
# que barram a release no Trivy.
RUN apt-get update -qq \
 && apt-get install -yq --no-install-recommends --only-upgrade apt libapt-pkg7.0 zlib1g libzstd1 \
 && rm -rf /var/lib/apt/lists/*
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
# Nenhum pacote GPL/AGPL nem ferramenta de dev no runtime (ADR 057). Roda no build de cada
# plataforma, antes do push; o bind mount não deixa o script na imagem.
RUN --mount=type=bind,source=scripts/check_runtime_licenses.py,target=/tmp/check_runtime_licenses.py \
    /app/.venv/bin/python -I -B /tmp/check_runtime_licenses.py
ARG GS_SERVICE_VERSION=dev
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GS_SERVICE_VERSION=${GS_SERVICE_VERSION}
USER app
EXPOSE 8000
# Sem access log: o evento amplo `main` já registra cada requisição, sem IP completo (plano, 3.7).
# Dois tetos. O da aplicação (ConcurrencyLimitMiddleware, entrypoints/api/concurrency.py) atende 28
# requisições, perto do dobro do pool do banco (10 + 5), e recusa a 29ª em problem+json com
# Retry-After. O do uvicorn é a última barreira e responde 503 `text/plain`, sem Retry-After e antes
# do ASGI: 72 = 28 + a rajada de 40 do /api/ no Nginx (uma conexão por pedido, sem keepalive) + 3
# sondas isentas (/livez, /readyz) + 1, porque ele compara com `>=` contando a conexão atual. Assim
# a rajada de um cliente com a API no teto ainda recebe o problem+json, que custa pouco. Na parada, 8 s para terminar os
# pedidos em curso, mais 2 s para o lifespan fechar o pool: cabe em qualquer carência de 10 s ou
# mais, que é o padrão do Docker e do Compose (o serviço `api` não declara `stop_grace_period`).
# tests/test_api_server_limits.py confere as duas contas.
CMD ["uvicorn", "goblinshift.entrypoints.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header", \
     "--no-access-log", "--limit-concurrency", "72", "--timeout-graceful-shutdown", "8"]
