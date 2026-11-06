# GoblinShift

API de processamento de arquivos por receitas — imagens, tabelas CSV, lotes de downloads e PDFs — com resultados privados, manifesto e execução isolada. Projeto de portfólio e serviço aberto.

> Em construção. Fatias 0 a 6 (fundação e deploy, sandbox, CSV Janitor, fluxo durável, executor real sob gVisor, Image Forge e Downloads Detox, identidade e segurança web) concluídas; as fatias 7 a 10 (interface, abertura pública, PDF Press e Media Mill) estão na `main` com pendências que dependem da VPS ou de decisão do dono. O serviço segue fechado a cadastros ([ADR 023](docs/adr/023-cadastro-fechado-ate-a-abertura.md)). O estado de cada fatia está em [docs/GoblinShiftPlan.md](docs/GoblinShiftPlan.md#s3-9).

## Documentos

| Documento | Conteúdo |
|---|---|
| [Plano](docs/GoblinShiftPlan.md) | Requisitos, contrato HTTP, stack, arquitetura, implantação, fatias e decisões (ADRs) |
| [Referências](docs/GoblinShiftReferencias.md) | Livros e documentação oficial citados pelo plano |
| [Avaliação](docs/GoblinShiftAvaliacao.md) | Revisão do plano e rastreamento de cada problema até a seção que o resolve |

## Stack

Python 3.14 · FastAPI · PostgreSQL 18 (também como fila) · SQLAlchemy 2 · Alembic · OpenTelemetry + OpenObserve · Docker Compose + Nginx · executor isolado com gVisor (a partir da fatia 1). Interface: React 19 · TypeScript 6 · Vite 8 · TanStack Query · Zod · Vitest (fatia 7).

## Desenvolvimento local

Requisitos: [uv](https://docs.astral.sh/uv/), Docker com Compose e o FFmpeg completo no `PATH` (`brew install ffmpeg` ou `apt install ffmpeg`), que os testes do Media Mill usam para gerar vídeos sintéticos.

```bash
uv sync                       # ambiente e dependências
./scripts/install-hooks.sh    # pre-commit que recusa commit com segredo (gitleaks)
./scripts/dev-secrets.sh      # segredos locais em .secrets/ e .env (não versionados)
docker compose up --build     # postgres, migrate, api, worker, maintenance, nginx, otel-collector, openobserve
curl http://localhost/readyz
```

A interface fica em `http://localhost/`. O Nginx sai da imagem `docker/web.Dockerfile`, que constrói a SPA num estágio Node e a serve com CSP estrita e os cabeçalhos de segurança de [3.6](docs/GoblinShiftPlan.md#s3-6); `/api/`, `/auth/`, `/livez` e `/readyz` vão para a API, `robots.txt` e os assets do build são arquivos de verdade (o que faltar é 404), e qualquer outra rota cai no `index.html`. Texto (HTML, CSS, JS, JSON, SVG, manifest) sai com gzip. `/docs` (Swagger UI servido pela própria API, sem CDN e com CSP própria) e `/openapi.json` também vão para a API. Em `/auth/*` vale um `limit_req` de 10 por minuto por cliente (rajada de 10), contado pelo IP real que só a borda pode informar (ADR 014). Depois de mudar `web/` ou `docker/nginx/`, `docker compose up --build` reconstrói a imagem.

O worker local usa uma imagem própria, `goblinshift-worker-dev` ([`docker/worker-dev.Dockerfile`](docker/worker-dev.Dockerfile)): a imagem da aplicação mais o `ffmpeg` e o `ffprobe` copiados do estágio `ffmpeg` de `docker/executor.Dockerfile`, os mesmos binários da produção (FFmpeg 8.1.2 mínimo, só `mov`/`matroska` e `file`/`pipe`; [ADR 036](docs/adr/036-media-mill-ffmpeg-minimo-no-executor.md)), para o Media Mill rodar com o `docker compose up` puro. O estágio vira o serviço `ffmpeg-minimo`, com zero réplicas: o Compose o constrói, mas não o executa. Sem cache, o primeiro `--build` compila o FFmpeg (alguns minutos); depois o cache do BuildKit, o mesmo da imagem do executor, resolve em segundos. A imagem de produção da aplicação continua sem FFmpeg (`tests/test_compose_dev_ffmpeg.py`).

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

Requisitos: Node 24 (versão em `web/.nvmrc`). Telas: Oficina (receitas, presets e execuções recentes), Bancada (arquivos e parâmetros), Execução (estado, pré-visualizações e downloads), Histórico e Acesso.

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

O palco 3D também tem uma suíte independente de [WebKit móvel](docs/testing/webkit-palco-3d.md), com `npm run e2e:webkit` e job próprio; a emulação não substitui a homologação em Safari/iOS físico.

As specs rodam no Chromium, em desktop (1280 px) e celular (400 px): as jornadas das quatro receitas (arquivos, reordenar só por botões, parâmetros, executar, baixar), o Histórico (execuções recentes na Oficina, Carregar mais pelo cursor, filtros na URL), axe WCAG 2.2 A/AA com contraste nos dois temas, anel de foco e ordem do Tab, movimento reduzido, troca de tema sem piscar, CLS e CSP sem violação. O build de `dist/` é servido com os cabeçalhos lidos de `docker/nginx/snippets/spa-headers.conf`, a mesma CSP de produção, e a API é falsa, dentro do processo do Playwright (`e2e/support/fakeApi.ts`), com os tipos da spec.

A chave de API só existe no processo de teste: cada pedido para `/api/v1/` ganha o `Authorization` por rota do Playwright, e uma spec confere que a chave, um canário `CANARY-<uuid>`, não aparece no HTML, nos assets, no storage nem nos cookies. Sem a chave, a API recusa com 401 e a Bancada mostra o aviso de login em breve, sem repetir a leitura.

```bash
npx playwright install --only-shell chromium   # uma vez
npm run build && npm run e2e
npm run e2e:sabotage                           # Devil's Advocate: planta um defeito por spec e exige a falha
E2E_BASE_URL=http://localhost GS_E2E_API_KEY=... npm run e2e          # contra o Compose, com a API real
E2E_VPS=1 E2E_BASE_URL=https://goblinshift.dev GS_E2E_API_KEY=... npm run e2e   # contra a VPS, só leitura
```

No modo remoto, as specs que dependem de estado forçado na API falsa (as matrizes de telas por estado, o CLS da Execução e a jornada do Media Mill, cujos MP4 de fixture são falsos) são puladas, e a suíte roda com um worker: a conta tem uma execução ativa por vez. Contra o Compose, a conta sai do `gs-admin` com `create-user` e `issue-key`, com todos os escopos (`--scopes files:read,files:write,runs:read,runs:write,presets:read,presets:write`). Cada rodada consome 12 das 30 execuções diárias da conta; para repetir mais vezes na mesma conta local, use `gs-admin set-limits --user <id> --runs-per-24h <n>`.

Esse modo foi exercitado em 2026-11-05 contra o Compose local em um Mac: 116 testes passaram e 418 foram pulados, em desktop e celular, com envio, fila, receita e download pela API real, numa conta nova. O Compose usa o Runner local, um subprocesso do worker: o caminho navegador → API real com gVisor continua sem rodada. A chave entra por rota do Playwright, e o Chromium baixa `a[download]` por fora dela, então só nesse modo a spec tira o atributo antes do clique.

Segredos nunca entram no repositório: os de desenvolvimento ficam em `.secrets/` e `.env` (ignorados), os de produção só na VPS. O hook de pre-commit (`.githooks/pre-commit`) roda o gitleaks nas mudanças preparadas, com a mesma imagem e configuração da CI, e recusa o commit antes que um segredo saia da máquina. A CI repete a varredura no histórico inteiro antes de publicar a imagem, e o GitHub tem secret scanning e push protection ligados.

Dependências e código também passam por análise própria, fora do `ci.yml` e do gate da release ([ADR 042](docs/adr/042-auditoria-de-dependencias-e-codeql-fora-do-gate.md)): o `pip-audit` lê o `uv.lock` inteiro e o CodeQL analisa Python e TypeScript, em todo PR e toda semana.

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
| `media-mill` | 1 | Extrai uma faixa de áudio em WAV ou FLAC de 16 bits, na taxa e nos canais de origem, e gera 0, 1 ou 3 miniaturas JPEG de até 640 px em `thumbnails/`, de MP4/MOV (H.264 + AAC/PCM) e WebM (VP8/VP9 + Opus/Vorbis) conferidos por assinatura e ffprobe ([1.5](docs/GoblinShiftPlan.md#s1-5), [ADR 036](docs/adr/036-media-mill-ffmpeg-minimo-no-executor.md)) | 5 vídeos de até 256 MiB (512 MiB por lote), 300 s, 1920 × 1080, 60 fps, 48 kHz e estéreo |

Toda execução publica, além do conteúdo, o `report.json`, o `manifest.json` (entradas, hashes, parâmetros, digest da imagem do executor, duplicatas e rejeições) e o ZIP determinístico `<receita>.zip`, montados pelo empacotador comum no executor ([ADR 019](docs/adr/019-layout-de-out-pacote-e-papeis.md)). Uma saída idêntica a uma entrada não ocupa espaço de novo: o artefato aponta para o objeto do upload ([ADR 020](docs/adr/020-detox-reaproveita-o-objeto-da-entrada.md)). A versão de cada receita e os limites efetivos estão no OpenAPI de `POST /runs`.

## Presets, pré-visualizações e remoção de resultados

**Presets** ([plano, 1.6](docs/GoblinShiftPlan.md#s1-6)) são configurações privadas da conta: receita, versão e parâmetros, sem arquivos. `POST /api/v1/presets` (`201`, com `Location` e `ETag`), `GET /presets` (todos, em ordem de nome), `GET /presets/{id}`, `PUT /presets/{id}` (substituição integral) e `DELETE /presets/{id}` (`204`) usam os escopos `presets:read` e `presets:write`. O corpo é o de `POST /runs` sem `input_upload_ids` e com `name`; campo desconhecido é recusado e os parâmetros passam pela validação da versão pedida, como numa execução (`422 VALIDATION_FAILED` por campo, sob `parameters.`; versão retirada dá `422 RECIPE_VERSION_RETIRED`). O preset guarda os parâmetros como enviados, e os defaults são resolvidos de novo a cada execução.

- *Nome:* aparado e normalizado em NFC antes de validar, de 1 a 100 caracteres, sem caracteres de controle nem de formato invisível, e único na conta (a chave natural é `(dono, nome)`: repetir dá `409`, também depois de aparado).
- *Limite:* 100 presets por conta; o 101º dá `409`.
- *Revisão:* o `ETag` é a revisão entre aspas (`"3"`) e cada `PUT` soma 1. `PUT` e `DELETE` exigem `If-Match` com ele: ausente ou `*` → `428`; revisão que não é a atual → `412`, sem gravar nada; fora do formato → `400`. A revisão é conferida antes do nome, então quem leu uma versão velha recebe `412` e relê.
- *Dono:* preset de outra conta e inexistente dão o mesmo `404`. Excluir a conta remove os presets.
- *Executar:* `POST /presets/{id}/runs` recebe só `input_upload_ids`, exige `Idempotency-Key`, os escopos `runs:write` e `presets:read` e devolve `202`. A execução guarda o `preset_id` e uma cópia da configuração: editar ou excluir o preset depois não a altera (a referência vira nula).

Na interface, a Bancada tem "Salvar preset", que pede o nome e guarda os parâmetros do formulário, e a Oficina lista os presets (só com sessão) com "Abrir na Bancada", que pré-preenche os parâmetros (`?preset=<id>`), e "Excluir", com confirmação. Revisão vencida na exclusão (`412`) vira aviso e a lista é relida. A edição (`PUT`) existe só na API.

**Pré-visualizações** ([ADR 037](docs/adr/037-previews-em-grupo-e-papel-proprios.md)) são derivados pequenos que a receita grava em `previews/`, com o papel `preview`, depois de todo o conteúdo e só enquanto cabem nos tetos (faltar espaço nunca falha a execução). Ficam fora do manifesto e do ZIP. O CSV Janitor gera uma amostra CSV (cabeçalho e até 100 registros, até 64 KiB, fórmulas escapadas), o Image Forge um JPEG de até 512 × 512, o PDF Press o trecho do `page-map.json` de cada documento e o Media Mill usa cada miniatura (JPEG de até 640 px) como o preview dela; o Downloads Detox não gera. Preview acima do teto do formato (512 KiB o JPEG, 64 KiB a amostra e o mapa) é pulado, como quando falta espaço. `GET /runs/{id}/results` lista os previews com `source_artifact_id` apontando a saída de origem, e `GET /api/v1/artifacts/{id}/preview` (escopo `files:read`) recebe o id da **saída** e entrega o preview dela, como `image/jpeg` para a imagem e como bytes opacos para o resto, sempre com `attachment`, `nosniff`, CSP `sandbox` e `no-store`. Sem preview, ou de outra conta, é `404`; saída excluída ou vencida, `410`. A Execução mostra o preview ao lado do download da saída, sempre pelo `<img>` na rota de preview ou pela leitura do corpo pequeno, nunca `fetch` → Blob do download.

**Remoção de resultados:** `DELETE /api/v1/runs/{id}/artifacts` (escopo `files:write`) apaga antes das 48 h as saídas, o relatório, o manifesto, o pacote e os previews de uma execução terminal. Responde `202` e repetir também é `202`: os artefatos ficam indisponíveis na hora e a manutenção remove os arquivos quando nenhum download aberto os usa. Execução que ainda não terminou dá `409`; inexistente ou de outra conta, `404`. Os originais enviados não mudam. Na Execução, "Excluir resultados" pede confirmação.

## Contas e chaves de API (administrador)

O login web (Google ou GitHub) cria a conta no primeiro acesso, e a sessão emite chaves em `POST /api/v1/api-keys`. O administrador também cria contas e emite chaves pela CLI, que lê as mesmas variáveis `GS_*` da API:

```bash
uv run gs-admin create-user --display-name "Ana"            # imprime o id da conta
uv run gs-admin issue-key --user <id> \
  --scopes files:read,files:write,runs:read,runs:write --days 30 > chave.txt
uv run gs-admin revoke-key --key <public_id>
uv run gs-admin block --user <id>                          # roteiro: docs/runbooks/abuso.md
uv run gs-admin request-deletion --user <id>               # como DELETE /me, também para conta bloqueada
uv run gs-admin find-user --user-hash <hash>               # user.hash da telemetria → id da conta (- lê da entrada)
curl -H "Authorization: Bearer $(cat chave.txt)" https://…/api/v1/me
```

Com a pilha no Compose, a CLI roda no container da API: `docker compose exec api gs-admin …` localmente. Na VPS, o comando vai direto ao container em execução ([veja abaixo](#comandos-manuais-na-vps)): `sudo docker exec -i goblinshift-api-1 gs-admin …`.

A chave tem o formato `gsk_<public_id>_<segredo>`: 16 dígitos hexadecimais públicos, que localizam a chave, e 256 bits aleatórios em base64url. O segredo aparece uma única vez na saída padrão (o identificador e a expiração vão para stderr); o banco guarda só o SHA-256. Validade padrão de 30 dias, máxima de 90. Contas criadas pelo `gs-admin` são do operador e não passam pelo aceite dos termos; as do login web recebem `403 TERMS_NOT_ACCEPTED` até aceitar a versão vigente pela sessão (`POST /api/v1/me/terms:accept`; [ADR 029](docs/adr/029-aceite-versionado-dos-termos.md)). Erros da API saem em `application/problem+json` (RFC 9457), com `type` em `https://goblinshift.dev/problems/<código-em-kebab>` e o `request_id` também no header `X-Request-ID`.

## Publicação em produção

A produção é publicada só pelo Coolify ([`deploy/coolify/README.md`](deploy/coolify/README.md)). O caminho antigo por SSH está morto: o `deploy/deploy.sh` só recusa, o `deploy/release.sh` não tem quem o chame e o job `deploy` da CI fica pulado, porque o gate de release exige esse resultado (`deploy/coolify/release/gate.py`).

**Em produção**, a release grava as imagens novas no recurso da aplicação, o Coolify executa `compose up -d` e o receptor só registra sucesso depois de conferir a saúde e a imagem dos containers ([`deploy/coolify/release/receiver.py`](deploy/coolify/release/receiver.py)). Cada serviço tem `container_name` fixo em `deploy/coolify/app.json`, então a API e o Nginx, que leva o build da SPA, são substituídos, sem duas versões lado a lado. Nada garante uma troca sem queda. Enquanto o Nginx volta, a borda repete a falha de conexão por até 5 s (`lb_try_duration` no site do GoblinShift, montado pelo `deploy/coolify/gateway.json`). Depois da publicação manual do gateway, esse prazo passa a 25 s e o fim dele vira um 503 ([ADR 044](docs/adr/044-espera-no-gateway-na-troca-de-containers.md)); o merge sozinho não muda a produção. Nenhum dos dois cobre a API fora do ar com o Nginx de pé. Não há medição registrada da indisponibilidade de uma publicação em produção, e o repositório não tem número para ela.

**Na pilha local**, o `deploy/rollout-api.sh` sobe a API nova ao lado da antiga e só para a antiga depois que a nova está saudável e o Nginx já a enxerga. Ele depende de escalar o serviço `api`, o que o `container_name` fixo da produção impede, e não faz parte da publicação: só o `scripts/check-zero-downtime.sh` e o `deploy/release.sh`, morto, o chamam, e a CI não roda essa prova. A única medição registrada é local, de 2026-10-13 (commit `1719798`): 0 falhas em 7083 requisições a `/readyz`, em quatro trocas, contra 34 respostas 502 em 1309 no jeito anterior, um `compose up` com o Nginx fixando o IP da API. Esses números não dizem nada sobre a produção. Para repetir a prova, com a pilha de pé:

```bash
./scripts/check-zero-downtime.sh   # troca a API sob carga e falha se alguma resposta não for 200
```

Logs dos containers giram por serviço no próprio Compose (driver `local`, até 50 MiB por serviço; [plano, 1.7](docs/GoblinShiftPlan.md#s1-7)). O `/etc/docker/daemon.json` da VPS é compartilhado com o outro projeto e não é alterado. Os containers do executor (`gs-exec-*`), criados fora do Compose pelo `executor-ctl`, trazem o limite no próprio perfil fixo (`deploy/executor_ctl/profile.py`): driver `local` com um arquivo de 1 MiB, conferido pelo teste de aceitação no `HostConfig.LogConfig`. O log de acesso do Nginx registra o cliente com IP truncado e o caminho sem query string; a API não tem access log, porque o evento `main` já cobre cada requisição.

### Comandos manuais na VPS

O Coolify é dono dos containers `goblinshift-*`. Comandos manuais vão direto a eles, como root:

```bash
sudo docker ps --filter name=goblinshift-
sudo docker logs --tail 100 goblinshift-worker-1
sudo docker exec -i goblinshift-api-1 gs-admin create-user --display-name "Ana"
sudo docker exec -i goblinshift-postgres-1 psql -U goblinshift -d goblinshift -c 'SELECT count(*) FROM app_user'
```

> **Perigo: não opere pela cópia em `/srv/goblinshift/app`.** Ela parou em 2026-10-21 e não é Git. Pelo `deploy/compose.sh` dela, comandos de leitura funcionam, mas `up`, `run`, `pull` e `down` recriam 9 serviços com imagem antiga, incluindo o PostgreSQL. O `secrets/release.env` está congelado desde então, e o `/srv/goblinshift/executor/images.json` é velho: a allowlist viva é `/var/lib/coolify-release/goblinshift/executor-images.json`. Rodar `install.sh` ou `acceptance.*` a partir dela regride `/opt/goblinshift` e `/usr/local/lib/goblinshift`, e o instalador da aceitação daquela versão apaga o `pg-backup.sh` do timer de backup.

### Checkout temporário na VPS

Instaladores e `acceptance.*` rodam de um checkout temporário da `main` atual, descartado no fim:

```bash
gs_tmp="$(mktemp -d /tmp/goblinshift.XXXXXX)"
chmod 755 "$gs_tmp"
(umask 022 && git clone --quiet --depth 1 https://github.com/BipoXDXD/GoblinShift.git "$gs_tmp/repo")
git -C "$gs_tmp/repo" log -1 --format='%h %s'   # confira o commit antes de rodar
# … os comandos que citam "$gs_tmp" nas seções abaixo …
sudo rm -rf "$gs_tmp"
```

O `chmod 755` é necessário: o `mktemp -d` cria o diretório com 700, e quem precisa ler o checkout não é o root. Os instaladores o leem com os privilégios do `goblinshift`, e as sondas da aceitação e o gerador do Image Forge montam arquivos dele em containers com outro UID.

### Segredos do login

Em produção, o login web ([plano, 3.6](docs/GoblinShiftPlan.md#s3-6)) lê a configuração de variáveis privadas do Coolify ([`deploy/coolify/README.md`](deploy/coolify/README.md)) e os segredos de arquivos em `/srv/goblinshift/secrets/` (diretório `700`, dono `goblinshift`). Os arquivos ficam com modo `644` dentro desse diretório, como o `db_password`, porque o container lê com o UID da aplicação; com `600` a API não sobe. Só o container `api` recebe esses valores, montados em `/run/secrets/<nome>` (`deploy/coolify/app.json`).

| Nome | Onde | Quem grava |
|---|---|---|
| `GS_PUBLIC_ORIGIN` (`https://goblinshift.dev`), `GS_GOOGLE_CLIENT_ID`, `GS_GITHUB_CLIENT_ID` | variável no Coolify | operador; os IDs não são segredo |
| `GS_SIGNUP_MODE` (padrão `closed`, [ADR 023](docs/adr/023-cadastro-fechado-ate-a-abertura.md)) | variável no Coolify | operador |
| `google_client_secret`, `github_client_secret` | arquivo | operador, com `read -rs` (abaixo) |
| `csrf_secret`, `login_cookie_secret` | arquivo | operador, com `openssl rand -hex 32` (abaixo); a publicação pelo Coolify não os cria |

Sem uma das variáveis, o Compose recusa o arquivo antes de qualquer comando. Se ainda assim a API subir sem um segredo, ela sai na partida com um erro que nomeia a variável.

```bash
sudo -u goblinshift -i
cd /srv/goblinshift/secrets
read -rs GOOGLE && printf '%s' "$GOOGLE" > google_client_secret && unset GOOGLE   # cole e Enter (não ecoa)
read -rs GH && printf '%s' "$GH" > github_client_secret && unset GH
chmod 644 google_client_secret github_client_secret
```

Rotação:
- *client secrets*: gerar um novo no console do provedor, sobrescrever o arquivo (mantendo o modo 644), rodar `sudo docker restart goblinshift-api-1`, conferir um login e só então revogar o antigo;
- `csrf_secret`: sobrescrever o arquivo com `openssl rand -hex 32` (mantendo o modo 644) e rodar `sudo docker restart goblinshift-api-1`. A API só lê esses arquivos na partida, e uma publicação do Coolify pode não recriá-la. Não apague o arquivo: nada o recria na publicação. Invalida os tokens CSRF em uso: a próxima mutação de cada sessão falha até um novo login;
- `login_cookie_secret`: o mesmo procedimento. Só derruba os logins em andamento.

## Imagem do executor

Cada tentativa roda num container próprio da imagem `ghcr.io/bipoxdxd/goblinshift-executor` ([`docker/executor.Dockerfile`](docker/executor.Dockerfile)), publicada pela CI com a mesma tag da aplicação (`sha-<12>`). Ela contém só o Python da base oficial (fixada por digest, sem pip), `goblinshift/__init__.py` e `goblinshift/processing/` com o bytecode já compilado, o Pillow 12.3.0 instalado do `uv.lock` com hashes por um estágio de build ([ADR 018](docs/adr/018-pillow-na-imagem-do-executor.md)) e o FFmpeg 8.1.2 compilado do fonte oficial (conferido por SHA-256) só com os demuxers `mov` e `matroska` e os protocolos `file` e `pipe`, sem componentes GPL ([ADR 036](docs/adr/036-media-mill-ffmpeg-minimo-no-executor.md)): nada de banco, web nem outras dependências de terceiros. Sem cache, construir a imagem leva alguns minutos por causa do FFmpeg; por isso os testes `executor_image` rodam na CI só no job `gvisor`. Roda como `61000:61001` com `ENTRYPOINT` fixo em `python -m goblinshift.processing.executor /work`.

Na publicação, o receptor do Coolify (`deploy/coolify/release/receiver.py`) grava o digest na allowlist do `executor-ctl` (`/var/lib/coolify-release/goblinshift/executor-images.json`), e a referência `…@sha256:…` chega aos containers em `GS_EXECUTOR_IMAGE`.

```bash
uv run pytest -m executor_image                            # constrói a imagem e a roda com runc
GS_TEST_RUNTIME=runsc uv run pytest -m executor_image      # o mesmo sob gVisor (job gvisor da CI)
```

## Teste de aceitação do ambiente (na VPS)

Prova, dentro do executor sob gVisor e com os limites do plano (1.7), que não há rede, que a área de 6 GiB acaba em `ENOSPC`, que memória acima de 2 GiB mata o processo, que um fork bomb para em 64 processos e que a aplicação não alcança o `docker.sock` ([ADRs 004, 005 e 015](docs/adr/)). Roda antes de abrir o serviço e a cada mudança no host, a partir do [checkout temporário](#checkout-temporário-na-vps):

```bash
sudo bash "$gs_tmp/repo/deploy/gvisor.sh"                 # instala o gVisor (já incluído no provision.sh)
sudo bash "$gs_tmp/repo/deploy/executor-ctl/install.sh"   # instala o executor-ctl (já incluído no provision.sh)
cd "$gs_tmp/repo/deploy" && sudo python3 -B -m acceptance.run  # sai com 0 só se todos passarem; -B: sem bytecode do root
```

O teste monta o `docker run` das sondas com o perfil de produção do `executor-ctl` (`deploy/executor_ctl/profile.py`) e confere que o serviço responde no socket. Rode com o worker parado: a área de trabalho só admite uma tentativa por vez. O próprio teste para o container `maintenance` durante as sondas e o religa no fim, porque a manutenção removeria a sonda como tentativa sem registro ([ADR 017](docs/adr/017-aceitacao-suspende-a-manutencao.md)); se o teste for morto à força, ele já terá impresso o `docker start` que religa a manutenção.

### Aceitação semanal, agendada

O `goblinshift-acceptance.timer` roda o mesmo teste uma vez por semana, no domingo às 07:30 UTC com até 30 min de sorteio, e pinga o check "aceitação semanal" do healthchecks.io (7 dias, tolerância de 2 h) (`/start`, sucesso ou `/fail`; tabela dos monitores em [`deploy/openobserve/README.md`](deploy/openobserve/README.md#monitores-externos)). O `deploy/acceptance/scheduled.sh` para o worker durante o teste e o religa no fim, também quando a aceitação reprova; se o worker não voltar, o resultado é `/fail` e o log traz o `docker start` que o religa. Nesses 10 a 30 minutos (ADR 017) as execuções esperam na fila.

```bash
sudo bash "$gs_tmp/repo/deploy/acceptance/install.sh"   # copia o código e cria /etc/goblinshift/acceptance.env vazio (root, 600)
sudoedit /etc/goblinshift/acceptance.env                # ACCEPTANCE_HEALTHCHECK_URL=<URL de ping do check>
sudo bash "$gs_tmp/repo/deploy/acceptance/install.sh"   # com a URL preenchida, liga o timer
sudo systemctl start goblinshift-acceptance.service   # roda já; resultado em journalctl -u goblinshift-acceptance
```

O serviço roda como root, então não executa nada de checkout nem lê nada de `/srv/goblinshift/secrets`, que pertence ao usuário `goblinshift`: quem controlasse esses caminhos rodaria código como root. O instalador copia `deploy/acceptance`, `deploy/executor_ctl` e `deploy/backup/lib.sh` para `/usr/local/lib/goblinshift` (do root, sem escrita para outros) recusa diretório de destino que outro usuário grave ou que seja symlink e lê o checkout com os privilégios do `goblinshift`, para que um symlink plantado ali não copie um arquivo que só o root lê. O `deploy/backup/lib.sh` entra em `/usr/local/lib/goblinshift/backup`, dividido com o timer de backup: o instalador troca só esse arquivo. Uma mudança na aceitação só chega ao timer quando o `install.sh` roda de novo, como no executor-ctl; rode-o a partir do [checkout temporário](#checkout-temporário-na-vps), nunca da cópia parada. A unit tem o sistema de arquivos somente leitura fora da área de trabalho e só as capabilities que as sondas usam (`systemd-analyze security goblinshift-acceptance.service`).

Não rode o teste de caos nem os do Image Forge e do Media Mill no horário do timer: todos usam a mesma área de trabalho.

### Pior caso do Image Forge (na VPS, antes de publicar uma versão)

Mede 100 JPEGs de 24 MP em Display P3 com orientação 6 e miniaturas numa tentativa com o perfil de produção (gVisor, 1 vCPU, 2 GiB), sem passar pela API: usa a imagem do executor, gera as fotos dentro dela e roda a tentativa na área de trabalho real, com a manutenção parada como nas sondas ([ADR 018](docs/adr/018-pillow-na-imagem-do-executor.md)). Rode com o worker parado, do [checkout temporário](#checkout-temporário-na-vps):

```bash
cd "$gs_tmp/repo/deploy" && sudo python3 -B -m acceptance.image_forge_load   # PASSOU só dentro de 600 s
```

Sem opções, o comando **constrói a imagem no host**, o que compila o FFmpeg e é pesado para a VPS que atende a produção: prefira [medir com a imagem publicada](#medir-com-a-imagem-publicada).

### Pior caso do Media Mill (na VPS, antes de publicar a versão)

A VPS não tem FFmpeg e a imagem do executor só tem o build mínimo, que não codifica H.264: os vídeos saem de uma máquina com o FFmpeg completo e vão para a VPS. Mede 5 vídeos de 300 s em 1080p60 com keyframe a cada 10 s, FLAC e 3 miniaturas numa tentativa com o perfil de produção, sem passar pela API, com a manutenção parada ([ADR 036](docs/adr/036-media-mill-ffmpeg-minimo-no-executor.md)). Rode com o worker parado:

```bash
./scripts/media-mill-worst-case.sh /tmp/pior-caso            # na máquina com FFmpeg (~375 MiB)
scp /tmp/pior-caso/*.mp4 stillon:/tmp/pior-caso/
cd "$gs_tmp/repo/deploy" && sudo python3 -B -m acceptance.media_mill_load /tmp/pior-caso   # PASSOU só dentro de 600 s
```

Como no Image Forge, sem opções o comando **constrói a imagem no host**: prefira a imagem publicada, abaixo.

### Medir com a imagem publicada

As duas medições usam a tag local `goblinshift-executor:medicao`. Com `--skip-build`, medem a imagem que já tem essa tag, sem `docker build`, e param se ela não existir. A tag é só um nome local: nada na produção a usa.

```bash
# A imagem em produção; antes de publicar, use ghcr.io/bipoxdxd/goblinshift-executor:sha-<12 dígitos do commit>.
image="$(sudo docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' goblinshift-worker-1 |
  sed -n 's/^GS_EXECUTOR_IMAGE=//p')"
sudo docker pull "$image"
sudo docker tag "$image" goblinshift-executor:medicao
cd "$gs_tmp/repo/deploy"
sudo python3 -B -m acceptance.image_forge_load --skip-build
sudo python3 -B -m acceptance.media_mill_load --skip-build /tmp/pior-caso
sudo docker rmi goblinshift-executor:medicao   # remove só a tag
```

O Image Forge gera as fotos com o `scripts/e2e_fixtures.py` do checkout: o checkout e a imagem devem ser do mesmo commit, ou da `main` compatível com ela.

### Teste de caos do worker (na VPS)

Prova o critério de pronto da fatia 4 ([plano, 3.7 e 3.9](docs/GoblinShiftPlan.md#s3-9)): o worker morto por SIGKILL e pausado além do lease em cada fronteira do protocolo (logo depois do claim, com o executor rodando e durante a coleta e publicação), sem publicação duplicada nem container órfão. Roda do [checkout temporário](#checkout-temporário-na-vps) contra a pilha de produção, com a fila vazia (o teste derruba o worker e recusa começar se houver execução não terminal, container de executor ou área de trabalho suja):

```bash
cd "$gs_tmp/repo/deploy" && sudo python3 -B -m acceptance.chaos   # sai com 0 só se todos passarem
sudo python3 -B -m acceptance.chaos --scenario sigstop/coleta          # só um cenário (repetível)
# --base-url muda a URL da API (padrão https://goblinshift.dev; a borda não atende pelo IP)
```

Para cada um dos 6 cenários, o teste emite uma chave de curta duração (só na memória, revogada no fim) para uma conta de teste criada pelo `gs-admin` (ou a de `--user <id>`), envia 5 tabelas de ~7,3 MiB e cria a execução; acha a fronteira pelo banco, numa sessão `psql` consultada a cada 20 ms; aplica `docker kill --signal=KILL` (e traz o mesmo container de volta com `docker start`) ou `docker pause` até a manutenção encerrar a tentativa congelada (o freezer do cgroup equivale ao SIGSTOP); e confere o estado `succeeded`, as tentativas e gerações, uma única publicação da geração final, o descarte dos candidatos vencidos, nenhum container `gs-exec-*` nem sobra em `/srv/goblinshift/exec-work`, e o download byte a byte. Leva de 15 a 20 minutos. A lógica pura está em `deploy/acceptance/chaos_rules.py`, testada localmente em `tests/acceptance/`; o script inteiro só roda na VPS.

Os testes do `executor-ctl` com Docker real usam o runtime de `GS_TEST_RUNTIME` (padrão `runc`); com o gVisor instalado: `GS_TEST_RUNTIME=runsc uv run pytest -m executor_ctl tests/executor_ctl`.

## Backup do PostgreSQL (fora da VPS)

Dump diário cifrado com `age`, enviado ao Backblaze B2 com uma chave só de escrita, e restauração testada todo mês no GitHub Actions ([ADR 022](docs/adr/022-backup-externo.md)). Antes de ligar:

1. Gere o par `age` fora da VPS (`age-keygen -o goblinshift-backup.key`), ponha a chave pública em `deploy/backup/age-recipients.txt` por PR e guarde a privada no gerenciador de senhas e no secret `BACKUP_AGE_IDENTITY`. Na troca de chave, rode o instalador do passo 4 depois do merge: o timer usa a cópia instalada.
2. No B2 (passo pendente, fora do repositório): bucket privado com **Object Lock ligado e retenção padrão em modo compliance de 30 dias**, mais a regra de ciclo de vida que esconde o arquivo aos 30 dias e apaga 1 dia depois. Uma chave para a VPS só com `writeFiles` (sem `writeFileRetentions`, `writeFileLegalHolds`, `bypassGovernance`, `deleteFiles`, `readFiles` nem `listFiles`) e uma para a restauração com `listFiles`, `readFiles` e `readFileRetentions`, que expõe o modo e o prazo da retenção. Sem o Object Lock, a chave que escreve sobrescreve ou esconde os dumps, e a restauração mensal reprova.
3. No healthchecks.io: os checks "backup diário" (1 dia, tolerância de 2 h) e "restauração mensal" (31 dias).
4. Na VPS, a partir do [checkout temporário](#checkout-temporário-na-vps): `sudo bash "$gs_tmp/repo/deploy/backup/install.sh"` copia o `pg-backup.sh`, o `lib.sh` e o `age-recipients.txt` para `/usr/local/lib/goblinshift/backup` (do root; a unit executa dali, sem depender de checkout) e cria `/srv/goblinshift/secrets/backup.env` vazio; preencha e rode de novo para ligar o timer. Teste já com `sudo systemctl start goblinshift-pg-backup.service`. O dump sai por `docker exec` no `goblinshift-postgres-1`, sem Compose.
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
GS_API_KEY=gsk_… ./scripts/e2e-media-mill.sh http://localhost       # FLAC, miniaturas, HLS, 5.1, MPEG-4
```

Contra a VPS, a URL é `https://goblinshift.dev`: a borda não encaminha o acesso pelo IP ao GoblinShift, para a chave não trafegar em texto claro ([ADR 014](docs/adr/014-proxy-de-borda-caddy.md)).

### Jornada com escrita em produção (GitHub Actions)

O workflow `e2e-producao.yml` roda o PDF Press e o Media Mill em `https://goblinshift.dev` depois de cada release bem-sucedida do Coolify, às quartas às 11:37 UTC e à mão, com a chave `E2E_VPS_API_KEY` do environment `production`, de uma conta de teste. Sucesso pinga o check "jornada com escrita" do healthchecks.io, e falha pinga `/fail` (secret de repositório `E2E_WRITE_HEALTHCHECK_URL`, [tabela dos monitores](deploy/openobserve/README.md#monitores-externos)).

Cada rodada cria 4 execuções, de um teto de 30 a cada 24 h. O passo `scripts/e2e-quota-check.sh` lê o `GET /me` antes de começar: sem folga na cota, com menos de 1016 MiB livres no armazenamento da conta (a aceitação reserva 1008 MiB por execução) ou com outra execução ativa na conta, a rodada é pulada sem ping; chave recusada, escopo ausente ou termos pendentes falham. Nada é apagado à mão, porque tudo expira sozinho: entradas e saídas 48 h depois do fim da execução, upload pronto sem uso em 24 h e incompleto em 1 h. Cada rodada envia cerca de 1,6 MiB, e o histórico das execuções sai em 90 dias, sem contar em cota nenhuma.

**A chave atual vence em 2027-02-03** (validade máxima de 90 dias). Rode tudo na máquina local, com o `gh` autenticado e acesso SSH de root à VPS. O `gs-admin` roda na VPS, dentro do container da API em execução, porque `/srv/goblinshift/app` pode estar defasado em relação à release publicada pelo Coolify. O `docker exec` vai sem `-t`: um TTY acrescentaria CRLF e misturaria o stderr ao token.

```bash
umask 077                                            # local: os dois arquivos só para o seu usuário
ssh root@<vps> docker exec -i goblinshift-api-1 gs-admin issue-key --user <id da conta de teste> \
  --scopes files:read,files:write,runs:read,runs:write --days 90 > chave.txt 2> chave.err
cat chave.err                                        # identificador e validade da chave nova; anote
grep -Ecx 'gsk_[0-9a-f]{16}_[A-Za-z0-9_-]{43}' chave.txt   # tem de imprimir 1
gh secret set E2E_VPS_API_KEY --env production < chave.txt
gh workflow run e2e-producao.yml                     # confere a chave nova
ssh root@<vps> docker exec -i goblinshift-api-1 gs-admin revoke-key --key <public_id antigo>
rm chave.txt chave.err
```

O `public_id` antigo é a parte de 16 dígitos depois de `gsk_` na chave anterior e fica registrado no `chave.err` da rotação anterior. Sem nenhum dos dois, ele aparece na lista de chaves da página `/acesso`, numa sessão da conta de teste. Revogue só depois que a rodada manual passar. Se o container tiver outro nome, ache-o com `ssh root@<vps> docker ps --filter name=api`.

Depois, atualize a data neste parágrafo e no cabeçalho do workflow.

As fixtures do Image Forge, do Detox, do PDF Press e do Media Mill saem de `uv run python -m scripts.e2e_fixtures <receita> <dir>`, sem baixar nada e com os mesmos bytes a cada execução (os vídeos do Media Mill exigem o FFmpeg completo no `PATH`, como os testes), junto com o `expected.json` que `scripts/e2e_check_package.py` confere no ZIP baixado só com a biblioteca padrão do host. `POLL_LIMIT` (padrão 150, a cada 2 s) aumenta a espera por execução. `./scripts/measure-image-forge.sh` mede o pior caso do Image Forge (100 JPEGs de 24 MP com miniaturas, ~430 MiB) e só passa se couber numa tentativa ([ADR 018](docs/adr/018-pillow-na-imagem-do-executor.md)).

Em desenvolvimento, o worker roda a receita num subprocesso do próprio container, sem gVisor (`GS_RUNNER=local`, em `compose.override.yaml`), com o FFmpeg mínimo copiado da imagem do executor. Em produção só existe o `executor-ctl` do host, que cria um container gVisor por tentativa com a imagem fixada na Run (`GS_EXECUTOR_IMAGE`, por digest); o worker e a manutenção recusam subir com o Runner local ([ADR 016](docs/adr/016-runner-local-na-vps-ate-a-fatia-4.md), substituído na fatia 4).

## Estrutura

```text
src/goblinshift/
  entrypoints/       API (/api/v1), processos gs-worker e gs-maintenance, CLIs gs-admin, gs-csv-janitor e gs-export-openapi
  platform/          configuração, banco, telemetria e compatibilidade de schema
  processing/        receitas (csv_janitor/, image_forge/, downloads_detox/, pdf_press/, media_mill/), empacotador e executor
  runs/              ciclo de vida da execução: aceitação, fila, fencing, worker, manutenção
  files/             storage, uploads, artefatos, usos, transferências e retenção
  catalog/           receitas e versões publicadas, schemas e defaults
  identity/          contas e chaves de API
  presets/           presets privados da conta, com revisão (criar, listar, substituir, excluir)
migrations/          Alembic (expand/contract)
docker/              imagens da aplicação, do executor e do Nginx com a SPA; OpenTelemetry Collector
deploy/              provisionamento da VPS, gVisor, publicação pelo Coolify, backup do banco, SLOs e alertas e teste de aceitação do ambiente
web/                 interface React (Vite + TypeScript); src/api é o único ponto que fala com a API
```

## Builds Docker e arquitetura

Use `make image` para construir e verificar imagens **nativas do daemon Docker ativo**.
O comando ignora uma seleção antiga em `DOCKER_DEFAULT_PLATFORM`; não fixa o projeto em AMD64 ou ARM64.

`make image-release` exporta arquivos em `.docker-artifacts/` com **linux/amd64 e linux/arm64**,
sem carregar uma imagem estrangeira sobre a tag local. `IMAGE_TAG=...` escolhe a tag dos artefatos.

Para builds personalizados, use `scripts/docker-build.sh local -f CAMINHO -t IMAGEM CONTEXTO`.
Para publicar quando autorizado, use `scripts/docker-build.sh publish -f CAMINHO -t REGISTRY/IMAGEM CONTEXTO`;
o comando verifica as duas plataformas no registry. Não substitua estes comandos por um build
AMD64 com `--load` no Mac: para uma entrega offline, use o modo `oci ARQUIVO.tar`.
A verificação isolada é `scripts/docker-build.sh check IMAGEM` (local) ou `published REGISTRY/IMAGEM` (release).
Para a pilha local, `make up` também seleciona a arquitetura nativa.
