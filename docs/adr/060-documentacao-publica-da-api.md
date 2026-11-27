# ADR 060. Documentação e contrato da API públicos em produção, com o limite da API

- **Estado:** Aceito (2026-11-21, por decisão do dono: D7 = a, plano `build-e-conteineres`)
- **Relacionado:** #442 (item 31b), #366

## Contexto

O Nginx encaminha `/docs`, `/docs/assets/` e `/openapi.json` à API, além das rotas de `/api/`.
Essas três locations, e também o `/livez`, não tinham `limit_req`. Um cliente em laço, sem
limite no Nginx, enchia as vagas da API (`ConcurrencyLimitMiddleware` corta em 28, e o uvicorn
recusa acima de `--limit-concurrency 72`), e os outros clientes passavam a receber 503. O
`proxy_buffering` ligado nessas rotas faz o Nginx esvaziar a API rápido, então a ameaça é o
volume de pedidos, não a leitura lenta.

## Decisão

- `/docs` e `/openapi.json` continuam públicos em produção. São o contrato de uma API pública e
  servem de referência viva para quem a integra. Não carregam dado de usuário.
- Todas as locations que incluem `proxy-api.conf` têm o limite da zona `api` (20 por segundo, rajada
  de 40) e devolvem o 429 em `application/problem+json` (`@rate_limited`). O `/readyz` mantém a
  zona própria.
- `tests/test_nginx_rate_limit_coverage.py` falha se alguma location nova encaminhar à API sem
  limite. Uma exceção precisa entrar em `RATE_LIMIT_EXEMPT` com a justificativa.

## Alternativas descartadas

- Desligar a documentação em produção: reduz a superfície, mas o cliente da API perde a referência.
  O mapa de rotas já é público: a spec está versionada no repositório (`web/openapi/openapi.json`).

## Consequências

- Quem sonda `/livez` com mais de 20 pedidos por segundo do mesmo cliente recebe 429. O monitor de
  uptime fica muito abaixo disso, e o `scripts/check-zero-downtime.sh` usa o `/readyz`.
