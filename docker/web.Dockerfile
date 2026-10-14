# Nginx do GoblinShift com o build da SPA (docs/GoblinShiftPlan.md, 2.2, 3.6 e 3.8). A imagem leva
# o build e a configuração que o serve juntos, para os dois nunca divergirem num deploy.
#
# Bases fixadas pelo digest do índice multiplataforma (amd64 na VPS e na CI, arm64 no Mac).
FROM node:24.21.0-trixie-slim@sha256:173f125896c3b47ddf056734c7ea789d04595a6a08769a8f78e0df642781fb66 AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# --ignore-scripts: nenhuma dependência precisa de script de instalação para o build.
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM nginx:1.30.5@sha256:9bf97bd7714f5e24c1ccd545ecb9eb5435cb6d109c97cebb15e7e455e0239edb
# Liga o pacote do GHCR ao repositório: ele herda a visibilidade e o acesso do Actions.
LABEL org.opencontainers.image.source=https://github.com/BipoXDXD/GoblinShift
# Só variáveis GS_* entram no template; as do Nginx ($uri, $host…) ficam intactas.
ENV NGINX_ENVSUBST_FILTER=^GS_ \
    GS_DOMAIN=_
RUN rm /etc/nginx/conf.d/default.conf /usr/share/nginx/html/*
COPY docker/nginx/templates/ /etc/nginx/templates/
COPY docker/nginx/snippets/ /etc/nginx/snippets/
COPY --from=build /web/dist/ /usr/share/nginx/html/
