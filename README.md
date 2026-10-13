# GoblinShift

API de processamento de arquivos por receitas — imagens, tabelas CSV e lotes de downloads — com resultados privados, manifesto e execução isolada. Projeto de portfólio e serviço aberto.

> Em construção. Fatia atual: **0 — fundação e deploy**. A ordem completa está em [docs/GoblinShiftPlan.md](docs/GoblinShiftPlan.md#s3-9).

## Documentos

| Documento | Conteúdo |
|---|---|
| [Plano](docs/GoblinShiftPlan.md) | Requisitos, contrato HTTP, stack, arquitetura, implantação, fatias e decisões (ADRs) |
| [Referências](docs/GoblinShiftReferencias.md) | Livros e documentação oficial citados pelo plano |
| [Avaliação](docs/GoblinShiftAvaliacao.md) | Revisão do plano e rastreamento de cada problema até a seção que o resolve |

## Stack

Python 3.14 · FastAPI · PostgreSQL 18 (também como fila) · SQLAlchemy 2 · Alembic · OpenTelemetry + OpenObserve · Docker Compose + Nginx · executor isolado com gVisor (a partir da fatia 1).

## Desenvolvimento local

Requisitos: [uv](https://docs.astral.sh/uv/) e Docker com Compose.

```bash
uv sync                       # ambiente e dependências
./scripts/dev-secrets.sh      # segredos locais em .secrets/ e .env (não versionados)
docker compose up --build     # postgres, migrate, api, nginx, otel-collector, openobserve
curl http://localhost/readyz
```

O OpenObserve fica em `http://127.0.0.1:5080`, com as credenciais do `.env`.

## Verificações (as mesmas da CI)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # modo estrito
uv run lint-imports           # contratos de arquitetura (docs/GoblinShiftPlan.md, 3.2)
uv run pytest                 # inclui PostgreSQL real via Testcontainers
```

## Estrutura

```text
src/goblinshift/
  entrypoints/api/   FastAPI: /livez, /readyz e, a partir da fatia 3, /api/v1
  platform/          configuração, banco, telemetria e compatibilidade de schema
  processing/ runs/ files/ catalog/ presets/ identity/   módulos de domínio (vazios até as próximas fatias)
migrations/          Alembic (expand/contract)
docker/              imagem da aplicação, Nginx e OpenTelemetry Collector
deploy/              provisionamento da VPS e script de deploy chamado pela CI
```
