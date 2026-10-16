# GoblinShift

API de processamento de arquivos por receitas — imagens, tabelas CSV, lotes de downloads e PDFs — com resultados privados, manifesto e execução isolada. Projeto de portfólio e serviço aberto.

> Em construção. Fatias 0 a 4 (fundação e deploy, sandbox, CSV Janitor, fluxo durável e executor real sob gVisor) concluídas; **5 — Image Forge e Downloads Detox** em revisão. A ordem completa está em [docs/GoblinShiftPlan.md](docs/GoblinShiftPlan.md#s3-9).

## Documentos

| Documento | Conteúdo |
|---|---|
| [Plano](docs/GoblinShiftPlan.md) | Requisitos, contrato HTTP, stack, arquitetura, implantação, fatias e decisões (ADRs) |
| [Referências](docs/GoblinShiftReferencias.md) | Livros e documentação oficial citados pelo plano |
| [Avaliação](docs/GoblinShiftAvaliacao.md) | Revisão do plano e rastreamento de cada problema até a seção que o resolve |

## Stack

Python 3.14 · FastAPI · PostgreSQL 18 (também como fila) · SQLAlchemy 2 · Alembic · OpenTelemetry + OpenObserve · Docker Compose + Nginx · executor isolado com gVisor (a partir da fatia 1). Interface: React 19 · TypeScript 6 · Vite 8 · TanStack Query · Zod · Vitest (fatia 7).

## Desenvolvimento local

Requisitos: [uv](https://docs.astral.sh/uv/) e Docker com Compose.

```bash
uv sync                       # ambiente e dependências
./scripts/install-hooks.sh    # pre-commit que recusa commit com segredo (gitleaks)
./scripts/dev-secrets.sh      # segredos locais em .secrets/ e .env (não versionados)
docker compose up --build     # postgres, migrate, api, worker, maintenance, nginx, otel-collector, openobserve
curl http://localhost/readyz
```

A interface fica em `http://localhost/`. O Nginx sai da imagem `docker/web.Dockerfile`, que constrói a SPA num estágio Node e a serve com CSP estrita e os cabeçalhos de segurança de [3.6](docs/GoblinShiftPlan.md#s3-6); `/api/`, `/auth/`, `/livez` e `/readyz` vão para a API, `robots.txt` e os assets do build são arquivos de verdade (o que faltar é 404), e qualquer outra rota cai no `index.html`. Texto (HTML, CSS, JS, JSON, SVG, manifest) sai com gzip. `/docs` (Swagger UI servido pela própria API, sem CDN e com CSP própria) e `/openapi.json` também vão para a API. Em `/auth/*` vale um `limit_req` de 10 por minuto por cliente (rajada de 10), contado pelo IP real que só a borda pode informar (ADR 014). Depois de mudar `web/` ou `docker/nginx/`, `docker compose up --build` reconstrói a imagem.

O OpenObserve fica em `http://127.0.0.1:5080`, com as credenciais do `.env`.

## Verificações (as mesmas da CI)

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy                   # modo estrito
uv run lint-imports           # contratos de arquitetura (docs/GoblinShiftPlan.md, 3.2)
uv run pytest                 # inclui PostgreSQL real via Testcontainers
```

Depois de mexer no worker, na manutenção ou no Runner, `./scripts/stress-runs.sh [suítes]` roda N suítes `tests/runs` em paralelo (padrão: 5), a carga em que os testes do worker como processo já falharam; sai com 1 e aponta o log de cada suíte vermelha.

### Interface web (`web/`)

Requisitos: Node 24 (versão em `web/.nvmrc`). Telas: Oficina (receitas), Bancada (arquivos e parâmetros) e Execução (estado e downloads).

Sistema visual em `web/src/styles/`: tokens em CSS custom properties (`tokens.css`: paleta Taverna escura, com madeira, cobre, âmbar, brasa e pátina em OKLCH, [ADR 032](docs/adr/032-paleta-taverna-escura.md); papéis semânticos nos temas claro e escuro), fontes auto-hospedadas sob OFL-1.1 ([`web/LICENCAS-FONTES.md`](web/LICENCAS-FONTES.md)) e base global (foco, controles, progresso, movimento reduzido). O tema segue o sistema, com escolha manual no cabeçalho; `public/theme-init.js` aplica o tema antes da primeira pintura, sem script inline. Testes travam o sistema: contraste WCAG de cada par nos dois temas (`contrast.test.ts`), só valores da escala e nenhum estilo inline (`classScale.test.ts`). A abertura tem as cinco goblins em 3D, uma por vez num seletor, com giro livre e inclinação por arrasto, setas e botão Girar (three.js, a Rúbia carregada depois do conteúdo e as outras só quando escolhidas, só com GPU e sem movimento reduzido, com a imagem estática como fallback), e as goblins também aparecem nos estados vazio, carregando, erro e sucesso ([ADR 027](docs/adr/027-3d-na-abertura.md), [ADR 030](docs/adr/030-motor-e-asset-do-3d-da-abertura.md) e [ADR 038](docs/adr/038-elenco-em-3d-na-abertura.md)). A arte é gerada por `scripts/arte/` a partir dos brutos fora do repositório, com proveniência e licença em [`docs/art/LICENSE-ART.md`](docs/art/LICENSE-ART.md), conferidas no CI.

```bash
cd web
npm ci
npm run typecheck             # tsc -b, separado do build (o Vite só transpila)
npm run lint                  # ESLint: typescript-eslint strict type-checked, jsx-a11y, react-hooks
npm run format:check          # Prettier
npm test                      # Vitest + Testing Library (jsdom)
npm run knip                  # código e dependências mortos
npm run type-coverage         # 100% sem any
npm run build
npm run budget                # orçamento de peso do build (JS, CSS, fontes e 3D da abertura)
```

O contrato da API fica versionado em `web/openapi/openapi.json` e os tipos TypeScript são gerados dele. Depois de mudar uma rota ou um schema da API:

```bash
uv run gs-export-openapi              # na raiz: regrava web/openapi/openapi.json
(cd web && npm run generate:api)      # regenera web/src/api/schema.d.ts
```

O pytest falha se o `openapi.json` não acompanhar a API, e o job "Interface web" do CI falha se os tipos não acompanharem o `openapi.json`.

#### No navegador (Playwright, `web/e2e/`)

As specs rodam no Chromium, em desktop (1280 px) e celular (400 px): as jornadas das quatro receitas (arquivos, reordenar só por botões, parâmetros, executar, baixar), o Histórico (execuções recentes na Oficina, Carregar mais pelo cursor, filtros na URL), axe WCAG 2.2 A/AA com contraste nos dois temas, anel de foco e ordem do Tab, movimento reduzido, troca de tema sem piscar, CLS e CSP sem violação. O build de `dist/` é servido com os cabeçalhos lidos de `docker/nginx/snippets/spa-headers.conf`, a mesma CSP de produção, e a API é falsa, dentro do processo do Playwright (`e2e/support/fakeApi.ts`), com os tipos da spec.

A chave de API só existe no processo de teste: cada pedido para `/api/v1/` ganha o `Authorization` por rota do Playwright, e uma spec confere que a chave, um canário `CANARY-<uuid>`, não aparece no HTML, nos assets, no storage nem nos cookies. Sem a chave, a API recusa com 401 e a Bancada mostra o aviso de login em breve, sem repetir a leitura.

```bash
npx playwright install --only-shell chromium   # uma vez
npm run build && npm run e2e
npm run e2e:sabotage                           # Devil's Advocate: planta um defeito por spec e exige a falha
E2E_BASE_URL=http://localhost GS_E2E_API_KEY=... npm run e2e          # contra o Compose, com a API real
E2E_BASE_URL=https://goblinshift.dev GS_E2E_API_KEY=... npm run e2e   # contra a VPS (só pelo domínio)
```

No modo remoto, as specs que dependem de estado forçado na API falsa (as matrizes de telas por estado e o CLS da Execução) são puladas. Esse modo ainda não foi exercitado contra a API real.

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
| `pdf-press` | 1 | Junta (`merge`), separa em partes (`split`) ou monta um PDF com páginas escolhidas, repetições incluídas (`select`), com o qpdf; publica o `page-map.json` com cada página. Recusa senha, formulário, assinatura, anexo e conteúdo ativo, e falha a execução inteira em PDF danificado ou página inexistente ([1.5](docs/GoblinShiftPlan.md#s1-5), [ADR 035](docs/adr/035-pdf-press-com-pikepdf.md)) | 20 PDFs, 256 MiB, 200 páginas de entrada, 400 de saída e 200 documentos |

Toda execução publica, além do conteúdo, o `report.json`, o `manifest.json` (entradas, hashes, parâmetros, digest da imagem do executor, duplicatas e rejeições) e o ZIP determinístico `<receita>.zip`, montados pelo empacotador comum no executor ([ADR 019](docs/adr/019-layout-de-out-pacote-e-papeis.md)). Uma saída idêntica a uma entrada não ocupa espaço de novo: o artefato aponta para o objeto do upload ([ADR 020](docs/adr/020-detox-reaproveita-o-objeto-da-entrada.md)). A versão de cada receita e os limites efetivos estão no OpenAPI de `POST /runs`.

## Contas e chaves de API (administrador)

O login web (Google ou GitHub) cria a conta no primeiro acesso, e a sessão emite chaves em `POST /api/v1/api-keys`. O administrador também cria contas e emite chaves pela CLI, que lê as mesmas variáveis `GS_*` da API:

```bash
uv run gs-admin create-user --display-name "Ana"            # imprime o id da conta
uv run gs-admin issue-key --user <id> \
  --scopes files:read,files:write,runs:read,runs:write --days 30 > chave.txt
uv run gs-admin revoke-key --key <public_id>
uv run gs-admin block --user <id>                          # roteiro: docs/runbooks/abuso.md
uv run gs-admin find-user --user-hash <hash>               # user.hash da telemetria → id da conta (- lê da entrada)
curl -H "Authorization: Bearer $(cat chave.txt)" https://…/api/v1/me
```

Com a pilha no Compose, a CLI roda no container da API: `docker compose exec api gs-admin …` localmente. Na VPS, todo comando do Compose passa pelo `deploy/compose.sh` (veja abaixo): `sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh exec api gs-admin …`.

A chave tem o formato `gsk_<public_id>_<segredo>`: 16 dígitos hexadecimais públicos, que localizam a chave, e 256 bits aleatórios em base64url. O segredo aparece uma única vez na saída padrão (o identificador e a expiração vão para stderr); o banco guarda só o SHA-256. Validade padrão de 30 dias, máxima de 90. Contas criadas pelo `gs-admin` são do operador e não passam pelo aceite dos termos; as do login web recebem `403 TERMS_NOT_ACCEPTED` até aceitar a versão vigente pela sessão (`POST /api/v1/me/terms:accept`; [ADR 029](docs/adr/029-aceite-versionado-dos-termos.md)). Erros da API saem em `application/problem+json` (RFC 9457), com `type` em `https://goblinshift.dev/problems/<código-em-kebab>` e o `request_id` também no header `X-Request-ID`.

## Deploy sem queda

O `deploy/deploy.sh` (chamado pela CI) migra o banco, sobe a API nova ao lado da antiga e só para a antiga depois que a nova está saudável e o Nginx já a enxerga (`deploy/rollout-api.sh`). O Nginx, que leva o build da SPA, é recriado a cada versão; enquanto ele volta, a borda repete a conexão (`lb_try_duration` em `deploy/edge/conf.d/goblinshift.caddy`). Para conferir localmente, com a pilha de pé:

```bash
./scripts/check-zero-downtime.sh   # troca a API sob carga e falha se alguma resposta não for 200
```

Logs dos containers giram por serviço no próprio Compose (driver `local`, até 50 MiB por serviço; [plano, 1.7](docs/GoblinShiftPlan.md#s1-7)). O `/etc/docker/daemon.json` da VPS é compartilhado com o outro projeto e não é alterado. Os containers do executor (`gs-exec-*`), criados fora do Compose pelo `executor-ctl`, trazem o limite no próprio perfil fixo (`deploy/executor_ctl/profile.py`): driver `local` com um arquivo de 1 MiB, conferido pelo teste de aceitação no `HostConfig.LogConfig`. O log de acesso do Nginx registra o cliente com IP truncado e o caminho sem query string; a API não tem access log, porque o evento `main` já cobre cada requisição.

### Comandos manuais na VPS

O Compose de produção depende da versão implantada (tag da aplicação e imagem do executor por digest), que o deploy grava em `/srv/goblinshift/secrets/release.env` (atômico, modo 600, ao lado do `app.env`). O `deploy/compose.sh` junta esse arquivo, os segredos, os dois arquivos do Compose e os diretórios de dados do host; o deploy usa o mesmo script. Rode como o usuário `goblinshift`:

```bash
sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh ps
sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh logs --tail 100 worker
sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh exec api gs-admin create-user --display-name "Ana"
```

Sem o arquivo (antes do primeiro deploy) o script para com uma mensagem, em vez de o Compose cair na imagem `dev`. Se um deploy falhar no meio, o arquivo guarda a versão daquela tentativa, que é a configuração aplicada por último ao projeto Compose.

### Segredos do login

O login web ([plano, 3.6](docs/GoblinShiftPlan.md#s3-6)) lê a configuração do `app.env` e os segredos de arquivos em `/srv/goblinshift/secrets/` (diretório `700`, dono `goblinshift`). Os arquivos ficam com modo `644` dentro desse diretório, como o `db_password`, porque o container lê com o UID da aplicação; com `600` a API não sobe. Só o container `api` recebe esses valores, montados em `/run/secrets/<nome>` (`compose.prod.yaml`).

| Nome | Onde | Quem grava |
|---|---|---|
| `GS_PUBLIC_ORIGIN` (`https://goblinshift.dev`), `GS_GOOGLE_CLIENT_ID`, `GS_GITHUB_CLIENT_ID` | `app.env` | operador; os IDs não são segredo |
| `GS_SIGNUP_MODE` (padrão `closed`, [ADR 023](docs/adr/023-cadastro-fechado-ate-a-abertura.md)) | `app.env` | operador |
| `google_client_secret`, `github_client_secret` | arquivo | operador, com `read -rs` (abaixo) |
| `csrf_secret`, `login_cookie_secret` | arquivo | `deploy/app-secrets.sh` (`openssl rand -hex 32`), uma vez, nunca trocados por ele |

Sem uma das variáveis, o Compose recusa o arquivo antes de qualquer comando; sem um dos arquivos, ou com ele ilegível pela API, o `deploy/app-secrets.sh` para o deploy com o nome do que falta. Nos dois casos isso acontece antes do `migrate`, sem mexer no banco. Se ainda assim a API subir sem um segredo, ela sai na partida com um erro que nomeia a variável.

```bash
sudo -u goblinshift -i
cd /srv/goblinshift/secrets
read -rs GOOGLE && printf '%s' "$GOOGLE" > google_client_secret && unset GOOGLE   # cole e Enter (não ecoa)
read -rs GH && printf '%s' "$GH" > github_client_secret && unset GH
chmod 644 google_client_secret github_client_secret
```

Rotação:
- *client secrets*: gerar um novo no console do provedor, sobrescrever o arquivo, reimplantar (re-run do job de deploy) e só então revogar o antigo;
- `csrf_secret`: apagar o arquivo e reimplantar (o deploy gera outro). Invalida os tokens CSRF em uso: a próxima mutação de cada sessão falha até um novo login;
- `login_cookie_secret`: apagar o arquivo e reimplantar. Só derruba os logins em andamento.

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

### Aceitação semanal, agendada

O `goblinshift-acceptance.timer` roda o mesmo teste uma vez por semana, no domingo às 07:30 UTC com até 30 min de sorteio, e pinga o check "aceitação semanal" do healthchecks.io (7 dias, tolerância de 2 h) (`/start`, sucesso ou `/fail`; tabela dos monitores em [`deploy/openobserve/README.md`](deploy/openobserve/README.md#monitores-externos)). O `deploy/acceptance/scheduled.sh` para o worker durante o teste e o religa no fim, também quando a aceitação reprova; se o worker não voltar, o resultado é `/fail` e o log traz o `docker start` que o religa. Nesses 10 a 30 minutos (ADR 017) as execuções esperam na fila.

```bash
sudo bash deploy/acceptance/install.sh       # copia o código e cria /etc/goblinshift/acceptance.env vazio (root, 600)
sudoedit /etc/goblinshift/acceptance.env     # ACCEPTANCE_HEALTHCHECK_URL=<URL de ping do check>
sudo bash deploy/acceptance/install.sh       # com a URL preenchida, liga o timer
sudo systemctl start goblinshift-acceptance.service   # roda já; resultado em journalctl -u goblinshift-acceptance
```

O serviço roda como root, então não executa nada do checkout nem lê nada de `/srv/goblinshift/secrets`, que pertencem ao usuário `goblinshift`: quem controlasse esses caminhos rodaria código como root. O instalador copia `deploy/acceptance`, `deploy/executor_ctl` e `deploy/backup/lib.sh` para `/usr/local/lib/goblinshift` (do root, sem escrita para outros) recusa diretório de destino que outro usuário grave ou que seja symlink e lê o checkout com os privilégios do `goblinshift`, para que um symlink plantado ali não copie um arquivo que só o root lê. Por isso uma mudança na aceitação só chega ao timer quando o `install.sh` roda de novo, como no executor-ctl; rode-o a partir de um checkout conferido (`git status` limpo, no commit implantado). A unit tem o sistema de arquivos somente leitura fora da área de trabalho e só as capabilities que as sondas usam (`systemd-analyze security goblinshift-acceptance.service`).

Não rode o teste de caos nem o do Image Forge no horário do timer: os três usam a mesma área de trabalho.

### Pior caso do Image Forge (na VPS, antes de publicar uma versão)

Mede 100 JPEGs de 24 MP em Display P3 com orientação 6 e miniaturas numa tentativa com o perfil de produção (gVisor, 1 vCPU, 2 GiB), sem passar pela API: constrói a imagem do executor do checkout, gera as fotos dentro dela e roda a tentativa na área de trabalho real, com a manutenção parada como nas sondas ([ADR 018](docs/adr/018-pillow-na-imagem-do-executor.md)). Rode com o worker parado:

```bash
cd /srv/goblinshift/app/deploy && sudo python3 -B -m acceptance.image_forge_load   # PASSOU só dentro de 600 s
```

### Teste de caos do worker (na VPS)

Prova o critério de pronto da fatia 4 ([plano, 3.7 e 3.9](docs/GoblinShiftPlan.md#s3-9)): o worker morto por SIGKILL e pausado além do lease em cada fronteira do protocolo (logo depois do claim, com o executor rodando e durante a coleta e publicação), sem publicação duplicada nem container órfão. Roda contra a pilha de produção, com a fila vazia (o teste derruba o worker e recusa começar se houver execução não terminal, container de executor ou área de trabalho suja):

```bash
cd /srv/goblinshift/app/deploy && sudo python3 -B -m acceptance.chaos   # sai com 0 só se todos passarem
sudo python3 -B -m acceptance.chaos --scenario sigstop/coleta          # só um cenário (repetível)
# --base-url muda a URL da API (padrão https://goblinshift.dev; a borda não atende pelo IP)
```

Para cada um dos 6 cenários, o teste emite uma chave de curta duração (só na memória, revogada no fim) para uma conta de teste criada pelo `gs-admin` (ou a de `--user <id>`), envia 5 tabelas de ~7,3 MiB e cria a execução; acha a fronteira pelo banco, numa sessão `psql` consultada a cada 20 ms; aplica `docker kill --signal=KILL` (e traz o mesmo container de volta com `docker start`) ou `docker pause` até a manutenção encerrar a tentativa congelada (o freezer do cgroup equivale ao SIGSTOP); e confere o estado `succeeded`, as tentativas e gerações, uma única publicação da geração final, o descarte dos candidatos vencidos, nenhum container `gs-exec-*` nem sobra em `/srv/goblinshift/exec-work`, e o download byte a byte. Leva de 15 a 20 minutos. A lógica pura está em `deploy/acceptance/chaos_rules.py`, testada localmente em `tests/acceptance/`; o script inteiro só roda na VPS.

Os testes do `executor-ctl` com Docker real usam o runtime de `GS_TEST_RUNTIME` (padrão `runc`); com o gVisor instalado: `GS_TEST_RUNTIME=runsc uv run pytest -m executor_ctl tests/executor_ctl`.

## Backup do PostgreSQL (fora da VPS)

Dump diário cifrado com `age`, enviado ao Backblaze B2 com uma chave só de escrita, e restauração testada todo mês no GitHub Actions ([ADR 022](docs/adr/022-backup-externo.md)). Antes de ligar:

1. Gere o par `age` fora da VPS (`age-keygen -o goblinshift-backup.key`), ponha a chave pública em `deploy/backup/age-recipients.txt` por PR e guarde a privada no gerenciador de senhas e no secret `BACKUP_AGE_IDENTITY`.
2. No B2 (passo pendente, fora do repositório): bucket privado com **Object Lock ligado e retenção padrão em modo compliance de 30 dias**, mais a regra de ciclo de vida que esconde o arquivo aos 30 dias e apaga 1 dia depois. Uma chave para a VPS só com `writeFiles` (sem `writeFileRetentions`, `writeFileLegalHolds`, `bypassGovernance`, `deleteFiles`, `readFiles` nem `listFiles`) e uma para a restauração com `listFiles`, `readFiles` e `readFileRetentions`, que expõe o modo e o prazo da retenção. Sem o Object Lock, a chave que escreve sobrescreve ou esconde os dumps, e a restauração mensal reprova.
3. No healthchecks.io: os checks "backup diário" (1 dia, tolerância de 2 h) e "restauração mensal" (31 dias).
4. Na VPS: `sudo bash deploy/backup/install.sh` cria `/srv/goblinshift/secrets/backup.env` vazio; preencha e rode de novo para ligar o timer. Teste já com `sudo systemctl start goblinshift-pg-backup.service`.
5. No GitHub (passo pendente, fora do repositório): environment `backup-restore` com **política de branches "só `main`"** (Deployment branches and tags → Selected branches → `main`), para que nenhum outro ref rode com a chave privada, e com os secrets `BACKUP_S3_READ_KEY_ID`, `BACKUP_S3_READ_KEY`, `BACKUP_AGE_IDENTITY` e `RESTORE_HEALTHCHECK_URL`, e as variáveis do repositório `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION` e `BACKUP_S3_BUCKET`. Rode o workflow "Restauração testada do backup" à mão uma vez e registre o resultado aqui.

Os testes (`uv run pytest tests/backup`) rodam os scripts reais contra PostgreSQL e S3 locais (SeaweedFS, com o mesmo Object Lock do bucket real) e precisam de `age` e `curl` no PATH.

## SLOs e alertas (OpenObserve)

Os três SLOs de [3.7](docs/GoblinShiftPlan.md#s3-7) (aceitação, execução e download), a queima do orçamento em duas janelas (1 h e 15 min, aviso a 2x e urgente a 4x), os alertas de causa (fila cheia sustentada, disco acima de 85%) e o heartbeat ficam em [`deploy/openobserve/`](deploy/openobserve/README.md): consultas SQL versionadas e um provisionamento idempotente pela API do OpenObserve, pelo túnel SSH. O runbook explica os segredos, as opções de canal e como provocar um alerta de queima e um de causa.

```bash
ssh -N -L 5080:127.0.0.1:5080 <usuário>@<vps> &   # o OpenObserve só escuta em 127.0.0.1 da VPS
cd deploy && uv run python -m openobserve provision --secrets-file ~/.config/goblinshift/alertas.env
cd deploy && uv run python -m openobserve sli --secrets-file ~/.config/goblinshift/alertas.env
```

Os testes (`uv run pytest -m openobserve`) sobem o OpenObserve e o Collector das versões do Compose e provam cada consulta com spans sintéticos de proporção conhecida e os alertas disparando, ou não, no agendador real.

## Execução de ponta a ponta

Com a pilha de pé e uma chave com os escopos `files:*` e `runs:*`, cada script faz upload, execução idempotente, acompanhamento, resultados e download só com `curl`. Cada um roda o mesmo pedido duas vezes e exige o mesmo SHA-256 do ZIP (determinismo, [3.9](docs/GoblinShiftPlan.md#s3-9)):

```bash
GS_API_KEY=gsk_… ./scripts/e2e-csv-janitor.sh http://localhost      # saída limpa byte a byte
GS_API_KEY=gsk_… ./scripts/e2e-image-forge.sh http://localhost      # P3, orientação, MPO, 16 bits, bomba
GS_API_KEY=gsk_… ./scripts/e2e-downloads-detox.sh http://localhost  # colisão de nomes, JAR, cópia, HTML
GS_API_KEY=gsk_… ./scripts/e2e-pdf-press.sh http://localhost        # split em partes, mapa de páginas
```

Contra a VPS, a URL é `https://goblinshift.dev`: a borda não encaminha o acesso pelo IP ao GoblinShift, para a chave não trafegar em texto claro ([ADR 014](docs/adr/014-proxy-de-borda-caddy.md)).

As fixtures do Image Forge, do Detox e do PDF Press saem de `uv run python -m scripts.e2e_fixtures <receita> <dir>`, sem baixar nada e com os mesmos bytes a cada execução, junto com o `expected.json` que `scripts/e2e_check_package.py` confere no ZIP baixado só com a biblioteca padrão do host. `POLL_LIMIT` (padrão 150, a cada 2 s) aumenta a espera por execução. `./scripts/measure-image-forge.sh` mede o pior caso do Image Forge (100 JPEGs de 24 MP com miniaturas, ~430 MiB) e só passa se couber numa tentativa ([ADR 018](docs/adr/018-pillow-na-imagem-do-executor.md)).

Em desenvolvimento, o worker roda a receita num subprocesso do próprio container, sem gVisor (`GS_RUNNER=local`, em `compose.override.yaml`). Em produção só existe o `executor-ctl` do host, que cria um container gVisor por tentativa com a imagem fixada na Run (`GS_EXECUTOR_IMAGE`, por digest); o worker e a manutenção recusam subir com o Runner local ([ADR 016](docs/adr/016-runner-local-na-vps-ate-a-fatia-4.md), substituído na fatia 4).

## Estrutura

```text
src/goblinshift/
  entrypoints/       API (/api/v1), processos gs-worker e gs-maintenance, CLIs gs-admin, gs-csv-janitor e gs-export-openapi
  platform/          configuração, banco, telemetria e compatibilidade de schema
  processing/        receitas (csv_janitor/, image_forge/, downloads_detox/, pdf_press/), empacotador e executor
  runs/              ciclo de vida da execução: aceitação, fila, fencing, worker, manutenção
  files/             storage, uploads, artefatos, usos, transferências e retenção
  catalog/           receitas e versões publicadas, schemas e defaults
  identity/          contas e chaves de API
  presets/           presets (próximas fatias)
migrations/          Alembic (expand/contract)
docker/              imagens da aplicação, do executor e do Nginx com a SPA; OpenTelemetry Collector
deploy/              provisionamento da VPS, gVisor, deploy chamado pela CI, backup do banco, SLOs e alertas e teste de aceitação do ambiente
web/                 interface React (Vite + TypeScript); src/api é o único ponto que fala com a API
```
