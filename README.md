# GoblinShift

API de processamento de arquivos por receitas — imagens, tabelas CSV e lotes de downloads — com resultados privados, manifesto e execução isolada. Projeto de portfólio e serviço aberto.

> Em construção. Fatias 0 a 4 (fundação e deploy, sandbox, CSV Janitor, fluxo durável e executor real sob gVisor) concluídas; **5 — Image Forge e Downloads Detox** em revisão. A ordem completa está em [docs/GoblinShiftPlan.md](docs/GoblinShiftPlan.md#s3-9).

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
./scripts/install-hooks.sh    # pre-commit que recusa commit com segredo (gitleaks)
./scripts/dev-secrets.sh      # segredos locais em .secrets/ e .env (não versionados)
docker compose up --build     # postgres, migrate, api, worker, maintenance, nginx, otel-collector, openobserve
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

Segredos nunca entram no repositório: os de desenvolvimento ficam em `.secrets/` e `.env` (ignorados), os de produção só na VPS. O hook de pre-commit (`.githooks/pre-commit`) roda o gitleaks nas mudanças preparadas, com a mesma imagem e configuração da CI, e recusa o commit antes que um segredo saia da máquina. A CI repete a varredura no histórico inteiro antes de publicar a imagem, e o GitHub tem secret scanning e push protection ligados.

## CSV Janitor pela linha de comando

A receita de tabelas ([1.3](docs/GoblinShiftPlan.md#s1-3)) é uma biblioteca pura em `goblinshift.processing.csv_janitor`, só com a biblioteca padrão. A CLI chama a mesma função que o executor vai chamar:

```bash
echo '{"input_delimiter": ";", "trim_columns": ["nome"], "deduplicate_by": ["email"]}' > params.json
uv run gs-csv-janitor --params params.json --output-dir limpas/ contatos.csv vendas.csv
```

Cada tabela aceita vira `limpas/<nome>.csv` (UTF-8 sem BOM, CRLF, aspas só quando necessárias); o relatório JSON sai na saída padrão, com contagens, avisos de fórmulas e o motivo de cada rejeição. Sai com 0 se todas foram limpas, 1 se alguma foi rejeitada e 2 para configuração ou caminhos inválidos. As fixtures em `tests/csv_janitor/fixtures/` servem de exemplos.

## Receitas

| Receita | Versão | O que faz | Limites próprios |
|---|---|---|---|
| `csv-janitor` | 1 | Limpa tabelas CSV ([1.3](docs/GoblinShiftPlan.md#s1-3)) | 5 tabelas de até 8 MiB |
| `image-forge` | 1 | Redimensiona JPEG e PNG sem ampliar, aplica a orientação EXIF, converte Display P3 para sRGB, usa o quadro principal do MPO, remove metadados e gera miniaturas opcionais em `thumbnails/` ([1.2](docs/GoblinShiftPlan.md#s1-2)) | 100 imagens, 24 MP e 8192 px por eixo, conferidos antes de decodificar |
| `downloads-detox` | 1 | Classifica por assinatura em `images/`, `tables/`, `text/` e `archives/`, deduplica por SHA-256 e tamanho, renomeia e recusa executáveis, scripts, HTML e SVG ([1.4](docs/GoblinShiftPlan.md#s1-4)) | 100 arquivos, 480 MiB por lote |

Toda execução publica, além do conteúdo, o `report.json`, o `manifest.json` (entradas, hashes, parâmetros, digest da imagem do executor, duplicatas e rejeições) e o ZIP determinístico `<receita>.zip`, montados pelo empacotador comum no executor ([ADR 019](docs/adr/019-layout-de-out-pacote-e-papeis.md)). Uma saída idêntica a uma entrada não ocupa espaço de novo: o artefato aponta para o objeto do upload ([ADR 020](docs/adr/020-detox-reaproveita-o-objeto-da-entrada.md)). A versão de cada receita e os limites efetivos estão no OpenAPI de `POST /runs`.

## Contas e chaves de API (administrador)

Até o login web da fatia 6, quem usa a API recebe uma chave emitida pela CLI do administrador, que lê as mesmas variáveis `GS_*` da API:

```bash
uv run gs-admin create-user --display-name "Ana"            # imprime o id da conta
uv run gs-admin issue-key --user <id> \
  --scopes files:read,files:write,runs:read,runs:write --days 30 > chave.txt
uv run gs-admin revoke-key --key <public_id>
curl -H "Authorization: Bearer $(cat chave.txt)" https://…/api/v1/me
```

Com a pilha no Compose, a CLI roda no container da API: `docker compose exec api gs-admin …` localmente. Na VPS, todo comando do Compose passa pelo `deploy/compose.sh` (veja abaixo): `sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh exec api gs-admin …`.

A chave tem o formato `gsk_<public_id>_<segredo>`: 16 dígitos hexadecimais públicos, que localizam a chave, e 256 bits aleatórios em base64url. O segredo aparece uma única vez na saída padrão (o identificador e a expiração vão para stderr); o banco guarda só o SHA-256. Validade padrão de 30 dias, máxima de 90. Erros da API saem em `application/problem+json` (RFC 9457), com `type` em `https://goblinshift.dev/problems/<código-em-kebab>` e o `request_id` também no header `X-Request-ID`.

## Deploy sem queda

O `deploy/deploy.sh` (chamado pela CI) migra o banco, sobe a API nova ao lado da antiga e só para a antiga depois que a nova está saudável e o Nginx já a enxerga (`deploy/rollout-api.sh`). Para conferir localmente, com a pilha de pé:

```bash
./scripts/check-zero-downtime.sh   # troca a API sob carga e falha se alguma resposta não for 200
```

### Comandos manuais na VPS

O Compose de produção depende da versão implantada (tag da aplicação e imagem do executor por digest), que o deploy grava em `/srv/goblinshift/secrets/release.env` (atômico, modo 600, ao lado do `app.env`). O `deploy/compose.sh` junta esse arquivo, os segredos, os dois arquivos do Compose e os diretórios de dados do host; o deploy usa o mesmo script. Rode como o usuário `goblinshift`:

```bash
sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh ps
sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh logs --tail 100 worker
sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh exec api gs-admin create-user --display-name "Ana"
```

Sem o arquivo (antes do primeiro deploy) o script para com uma mensagem, em vez de o Compose cair na imagem `dev`. Se um deploy falhar no meio, o arquivo guarda a versão daquela tentativa, que é a configuração aplicada por último ao projeto Compose.

## Imagem do executor

Cada tentativa roda num container próprio da imagem `ghcr.io/bipoxdxd/goblinshift-executor` ([`docker/executor.Dockerfile`](docker/executor.Dockerfile)), publicada pela CI com a mesma tag da aplicação (`sha-<12>`). Ela contém só o Python da base oficial (fixada por digest, sem pip), `goblinshift/__init__.py` e `goblinshift/processing/` com o bytecode já compilado, e o Pillow 12.3.0 instalado do `uv.lock` com hashes por um estágio de build ([ADR 018](docs/adr/018-pillow-na-imagem-do-executor.md)): nada de banco, web nem outras dependências de terceiros. Roda como `61000:61001` com `ENTRYPOINT` fixo em `python -m goblinshift.processing.executor /work`.

No deploy, `deploy/executor-image.sh` baixa a imagem da tag implantada, resolve o digest e o grava na allowlist do `executor-ctl` (`/srv/goblinshift/executor/images.json`, com o digest novo e o anterior); a referência `…@sha256:…` vai para o Compose em `GS_EXECUTOR_IMAGE`, gravada pelo `deploy/release-env.sh` no `release.env` do host.

```bash
uv run pytest -m executor_image                            # constrói a imagem e a roda com runc
GS_TEST_RUNTIME=runsc uv run pytest -m executor_image      # o mesmo sob gVisor (job gvisor da CI)
```

## Teste de aceitação do ambiente (na VPS)

Prova, dentro do executor sob gVisor e com os limites do plano (1.7), que não há rede, que a área de 6 GiB acaba em `ENOSPC`, que memória acima de 2 GiB mata o processo, que um fork bomb para em 64 processos e que a aplicação não alcança o `docker.sock` ([ADRs 004, 005 e 015](docs/adr/)). Roda antes de abrir o serviço e a cada mudança no host:

```bash
sudo bash deploy/gvisor.sh                 # instala o gVisor (já incluído no provision.sh)
sudo bash deploy/executor-ctl/install.sh   # instala o executor-ctl (já incluído no provision.sh)
cd deploy && sudo python3 -B -m acceptance.run  # sai com 0 só se todos passarem; -B: sem bytecode do root
```

O teste monta o `docker run` das sondas com o perfil de produção do `executor-ctl` (`deploy/executor_ctl/profile.py`) e confere que o serviço responde no socket. Rode com o worker parado: a área de trabalho só admite uma tentativa por vez. O próprio teste para o container `maintenance` durante as sondas e o religa no fim, porque a manutenção removeria a sonda como tentativa sem registro ([ADR 017](docs/adr/017-aceitacao-suspende-a-manutencao.md)); se o teste for morto à força, ele já terá impresso o `docker start` que religa a manutenção.

### Teste de caos do worker (na VPS)

Prova o critério de pronto da fatia 4 ([plano, 3.7 e 3.9](docs/GoblinShiftPlan.md#s3-9)): o worker morto por SIGKILL e pausado além do lease em cada fronteira do protocolo (logo depois do claim, com o executor rodando e durante a coleta e publicação), sem publicação duplicada nem container órfão. Roda contra a pilha de produção, com a fila vazia (o teste derruba o worker e recusa começar se houver execução não terminal, container de executor ou área de trabalho suja):

```bash
cd /srv/goblinshift/app/deploy && sudo python3 -B -m acceptance.chaos   # sai com 0 só se todos passarem
sudo python3 -B -m acceptance.chaos --scenario sigstop/coleta          # só um cenário (repetível)
```

Para cada um dos 6 cenários, o teste emite uma chave de curta duração (só na memória, revogada no fim) para uma conta de teste criada pelo `gs-admin` (ou a de `--user <id>`), envia 5 tabelas de ~7,3 MiB e cria a execução; acha a fronteira pelo banco, numa sessão `psql` consultada a cada 20 ms; aplica `docker kill --signal=KILL` (e traz o mesmo container de volta com `docker start`) ou `docker pause` até a manutenção encerrar a tentativa congelada (o freezer do cgroup equivale ao SIGSTOP); e confere o estado `succeeded`, as tentativas e gerações, uma única publicação da geração final, o descarte dos candidatos vencidos, nenhum container `gs-exec-*` nem sobra em `/srv/goblinshift/exec-work`, e o download byte a byte. Leva de 15 a 20 minutos. A lógica pura está em `deploy/acceptance/chaos_rules.py`, testada localmente em `tests/acceptance/`; o script inteiro só roda na VPS.

Os testes do `executor-ctl` com Docker real usam o runtime de `GS_TEST_RUNTIME` (padrão `runc`); com o gVisor instalado: `GS_TEST_RUNTIME=runsc uv run pytest -m executor_ctl tests/executor_ctl`.

## Execução de ponta a ponta

Com a pilha de pé e uma chave com os escopos `files:*` e `runs:*`, cada script faz upload, execução idempotente, acompanhamento, resultados e download só com `curl`. Cada um roda o mesmo pedido duas vezes e exige o mesmo SHA-256 do ZIP (determinismo, [3.9](docs/GoblinShiftPlan.md#s3-9)):

```bash
GS_API_KEY=gsk_… ./scripts/e2e-csv-janitor.sh http://localhost      # saída limpa byte a byte
GS_API_KEY=gsk_… ./scripts/e2e-image-forge.sh http://localhost      # P3, orientação, MPO, 16 bits, bomba
GS_API_KEY=gsk_… ./scripts/e2e-downloads-detox.sh http://localhost  # colisão de nomes, JAR, cópia, HTML
```

As fixtures do Image Forge e do Detox saem de `uv run python -m scripts.e2e_fixtures <receita> <dir>`, sem baixar nada e com os mesmos bytes a cada execução, junto com o `expected.json` que `scripts/e2e_check_package.py` confere no ZIP baixado só com a biblioteca padrão do host. `POLL_LIMIT` (padrão 150, a cada 2 s) aumenta a espera por execução. `./scripts/measure-image-forge.sh` mede o pior caso do Image Forge (100 JPEGs de 24 MP com miniaturas, ~430 MiB) e só passa se couber numa tentativa ([ADR 018](docs/adr/018-pillow-na-imagem-do-executor.md)).

Em desenvolvimento, o worker roda a receita num subprocesso do próprio container, sem gVisor (`GS_RUNNER=local`, em `compose.override.yaml`). Em produção só existe o `executor-ctl` do host, que cria um container gVisor por tentativa com a imagem fixada na Run (`GS_EXECUTOR_IMAGE`, por digest); o worker e a manutenção recusam subir com o Runner local ([ADR 016](docs/adr/016-runner-local-na-vps-ate-a-fatia-4.md), substituído na fatia 4).

## Estrutura

```text
src/goblinshift/
  entrypoints/       API (/api/v1), processos gs-worker e gs-maintenance, CLIs gs-admin e gs-csv-janitor
  platform/          configuração, banco, telemetria e compatibilidade de schema
  processing/        receitas (csv_janitor/, image_forge/, downloads_detox/), empacotador e executor
  runs/              ciclo de vida da execução: aceitação, fila, fencing, worker, manutenção
  files/             storage, uploads, artefatos, usos, transferências e retenção
  catalog/           receitas e versões publicadas, schemas e defaults
  identity/          contas e chaves de API
  presets/           presets (próximas fatias)
migrations/          Alembic (expand/contract)
docker/              imagem da aplicação, Nginx e OpenTelemetry Collector
deploy/              provisionamento da VPS, gVisor, deploy chamado pela CI e teste de aceitação do ambiente
```
