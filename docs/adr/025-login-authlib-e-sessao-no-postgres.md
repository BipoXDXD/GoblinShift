# ADR 025. Login com Authlib, sessão no PostgreSQL e defesas web com código próprio

- **Estado:** aceito na Fase 0 da fatia 6 (2026-10-14), decisão D-6 e escolhas de biblioteca do plano da fatia
- **Plano:** [2.1](../GoblinShiftPlan.md#s2-1), [3.3](../GoblinShiftPlan.md#s3-3), [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 003](003-sem-redis.md), [ADR 008](008-google-github-sem-vinculo-de-contas.md), [ADR 014](014-proxy-de-borda-caddy.md)

## Contexto

A fatia 6 traz o login por Google (OIDC) e GitHub (OAuth 2.0), a sessão web, o CSRF e os cabeçalhos de 3.6. A regra é não construir autenticação própria. O Authlib teve dois CVEs no `state` em modo cache (CVE-2025-68158, corrigido na 1.6.6, e CVE-2026-41425, corrigido na 1.6.11). O serviço ainda não tem domínio próprio.

## Decisão

- **Authlib `>= 1.6.12`** (integração Starlette) para o fluxo OAuth/OIDC: PKCE S256, `state`, `nonce` e validação do ID token. `httpx` entra como dependência direta, porque a integração o usa e o pacote não o declara.
- **Estado do login no PostgreSQL** (`login_state`, TTL de 10 min), pelo cache do Authlib. O `SessionMiddleware` do Starlette guarda só o marcador, no cookie `__Secure-gs_login` com `Path=/auth`, `SameSite=Lax`, `Secure`, `Max-Age=600` e segredo próprio (`GS_LOGIN_COOKIE_SECRET_FILE`). Com `Path=/auth`, o cookie nem chega às rotas da API.
- **Sessão server-side** em `web_session` (token opaco de 256 bits, só o hash no banco), no cookie `__Host-gs_session`. Revogação imediata, sem Redis (ADR 003).
- **`redirect_uri` sempre de `GS_PUBLIC_ORIGIN`**, nunca do header `Host`; a mesma origem é a referência da checagem de `Origin`. Enquanto não há domínio, o código depende só dessa variável, obrigatória e `https` em produção.
- **CSRF e cabeçalhos com código próprio**: double-submit assinado ligado à sessão mais Fetch Metadata e `Origin` (~40 linhas com `hmac`), e um middleware de cabeçalhos. **Middleware sempre ASGI puro**, nunca `BaseHTTPMiddleware`, que quebra o streaming dos downloads e os contextvars do OpenTelemetry.
- **Segredos de arquivos** (`*_FILE`, Compose secrets): sem qualquer um deles, a API não sobe em produção.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| `httpx-oauth` | Não valida ID token; usaria o userinfo do Google, que pede escopos além de `openid` |
| `httpx` + `joserfc` à mão | Seria autenticação própria |
| `fastapi-users` / `fastapi-sso` | Modelo de usuário e de sessão próprios, em conflito com `(issuer, subject)` e `web_session` |
| Sessão no cookie assinado do Starlette | Não revoga e o conteúdo é legível |
| Estado do login inteiro no cookie | Cookie assinado não é cifrado (3.6) |
| `starlette-csrf` | Não faz o double-submit ligado à sessão com Fetch Metadata |

## Consequências

- Um segredo a mais (`login_cookie_secret`); trocá-lo só invalida logins em andamento. Trocar `csrf_secret` invalida os tokens CSRF emitidos.
- As rotas de login são `async` sobre um projeto síncrono: o acesso ao banco nelas passa por `run_in_threadpool`.
- Safari em `http://localhost` recusa cookies `Secure`; o desenvolvimento local usa Chrome ou Firefox.

## Como verificar

- `tests/test_authlib_version.py`: piso no `pyproject.toml`, no lock e no ambiente.
- `tests/test_settings_secrets.py`: produção sem segredo, sem ID de cliente ou sem origem `https` não sobe.
- Frente A: callback com `state` válido sem o cookie de login do mesmo navegador é recusado (regressão do CVE-2026-41425), e o `redirect_uri` ignora `Host: evil.example`.
- Frente B: atributos dos cookies, CSRF por toda rota mutável da matriz e um teste de streaming do download com o middleware de cabeçalhos.
