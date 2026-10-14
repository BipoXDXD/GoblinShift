# GoblinShift

API de processamento de arquivos por receitas — imagens, tabelas CSV e lotes de downloads — com resultados privados, manifesto e execução isolada. Projeto de portfólio e serviço aberto.

> Em construção. Fatias 0 (fundação e deploy), 1 (protótipo do sandbox) e 2 (CSV Janitor como biblioteca pura) concluídas; próxima: **3 — fluxo durável de ponta a ponta**. A ordem completa está em [docs/GoblinShiftPlan.md](docs/GoblinShiftPlan.md#s3-9).

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

## CSV Janitor pela linha de comando

A receita de tabelas ([1.3](docs/GoblinShiftPlan.md#s1-3)) é uma biblioteca pura em `goblinshift.processing.csv_janitor`, só com a biblioteca padrão. A CLI chama a mesma função que o executor vai chamar:

```bash
echo '{"input_delimiter": ";", "trim_columns": ["nome"], "deduplicate_by": ["email"]}' > params.json
uv run gs-csv-janitor --params params.json --output-dir limpas/ contatos.csv vendas.csv
```

Cada tabela aceita vira `limpas/<nome>.csv` (UTF-8 sem BOM, CRLF, aspas só quando necessárias); o relatório JSON sai na saída padrão, com contagens, avisos de fórmulas e o motivo de cada rejeição. Sai com 0 se todas foram limpas, 1 se alguma foi rejeitada e 2 para configuração ou caminhos inválidos. As fixtures em `tests/csv_janitor/fixtures/` servem de exemplos.

## Deploy sem queda

O `deploy/deploy.sh` (chamado pela CI) migra o banco, sobe a API nova ao lado da antiga e só para a antiga depois que a nova está saudável e o Nginx já a enxerga (`deploy/rollout-api.sh`). Para conferir localmente, com a pilha de pé:

```bash
./scripts/check-zero-downtime.sh   # troca a API sob carga e falha se alguma resposta não for 200
```

## Teste de aceitação do ambiente (na VPS)

Prova, dentro do executor sob gVisor e com os limites do plano (1.7), que não há rede, que a área de 6 GiB acaba em `ENOSPC`, que memória acima de 2 GiB mata o processo, que um fork bomb para em 64 processos e que a aplicação não alcança o `docker.sock` ([ADRs 004, 005 e 015](docs/adr/)). Roda antes de abrir o serviço e a cada mudança no host:

```bash
sudo bash deploy/gvisor.sh                # instala o gVisor (já incluído no provision.sh)
sudo python3 -B deploy/acceptance/run.py  # sai com 0 só se todos passarem; -B: sem bytecode do root no checkout
```

O `executor-ctl` em `deploy/acceptance/` é o protótipo descartável da fatia 1; a fatia 4 o substitui pelo serviço do host.

## Estrutura

```text
src/goblinshift/
  entrypoints/api/   FastAPI: /livez, /readyz e, a partir da fatia 3, /api/v1
  platform/          configuração, banco, telemetria e compatibilidade de schema
  processing/        receitas; csv_janitor/ é o CSV Janitor, biblioteca pura
  runs/ files/ catalog/ presets/ identity/   módulos de domínio (vazios até as próximas fatias)
migrations/          Alembic (expand/contract)
docker/              imagem da aplicação, Nginx e OpenTelemetry Collector
deploy/              provisionamento da VPS, gVisor, deploy chamado pela CI e teste de aceitação do ambiente
```
