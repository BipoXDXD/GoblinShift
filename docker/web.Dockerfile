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
# Termos e privacidade entram no build a partir de docs/legal/ (web/src/legal), a fonte única.
COPY docs/legal/ /docs/legal/
RUN npm run build

FROM nginx:1.30.6@sha256:f12e4e280502f79dae4488c9f677ab3ebe8a7079818bbfbf3242f236bac64d34
# Fixed Debian security updates; the scanned image digest is the release boundary.
RUN apt-get update -q \
 && apt-get install -yq --no-install-recommends --only-upgrade \
      curl=8.14.1-2+deb13u7 libcurl4t64=8.14.1-2+deb13u7 libexpat1=2.9.0-1~deb13u1 libbrotli1=1.1.0-2+deb13u1 \
      apt=3.0.4 libapt-pkg7.0=3.0.4 \
 && rm -rf /var/lib/apt/lists/*
# Liga o pacote do GHCR ao repositório: ele herda a visibilidade e o acesso do Actions.
LABEL org.opencontainers.image.source=https://github.com/BipoXDXD/GoblinShift
# Só variáveis GS_* entram no template; as do Nginx ($uri, $host…) ficam intactas.
ENV NGINX_ENVSUBST_FILTER=^GS_ \
    GS_DOMAIN=_
RUN rm /etc/nginx/conf.d/default.conf /usr/share/nginx/html/*
COPY docker/nginx/templates/ /etc/nginx/templates/
COPY docker/nginx/snippets/ /etc/nginx/snippets/
COPY --from=build /web/dist/ /usr/share/nginx/html/
