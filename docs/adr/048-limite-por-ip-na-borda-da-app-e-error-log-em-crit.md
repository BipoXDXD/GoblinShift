# ADR 048. Limite por IP na borda da app e log de erro do Nginx em `crit`

- **Estado:** aceito (PRs #234 e #239, no lote de 10/11/2026)
- **Plano:** [3.7](../GoblinShiftPlan.md#s3-7) e [3.8](../GoblinShiftPlan.md#s3-8)
- **Relacionado:** [ADR 028](028-limite-de-requisicoes-no-postgresql.md) (limite por conta no PostgreSQL), [ADR 041](041-ip-do-cliente-atras-da-cloudflare.md) (IP real do cliente e chave por `/64`)

## Contexto

Antes do lote, o Nginx da app só tinha a zona `auth` de `limit_req`. `/api/` e `/readyz` não tinham limite. O limite por conta do ADR 028 só age depois da autenticação: pedidos anônimos de um cliente chegavam à API e ocupavam o pool do banco de todos. Cada sonda de `/readyz` pega uma vaga do pool (10 + 5) e faz duas consultas. O uvicorn rodava sem teto de concorrência nem prazo de parada graciosa.

A carência real da `api` na parada é 10 s: nem `compose.yaml`, nem `compose.prod.yaml`, nem `deploy/coolify/app.json` declaram `stop_grace_period` para ela (só o `worker` tem 30 s), e o padrão do Docker e do Compose é 10 s (relatório da frente 4h). Um prazo gracioso de 25 s, como se cogitou, nunca agiria: o SIGKILL chegaria antes.

Ao provar o limite novo com canários, a revisão do #234 achou outro problema. O `error_log` da imagem do Nginx está em `notice`, e em qualquer nível abaixo de `crit` cada linha de requisição traz `client:` com o IP completo e `request:` com a query string. Isso acontecia na recusa do `limit_req` (inclusive no `/auth` antigo), no 413 do teto de corpo e em toda falha de upstream. O log de acesso já tinha sido desenhado para truncar o IP e cortar a query (plano, 3.7), e o log de erro desfazia isso.

## Alternativas

**Limite na borda:**

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. `limit_req` por cliente em `/api/` e `/readyz` no Nginx, com teto de concorrência no uvicorn** (escolhida) | Recusa antes da autenticação e antes do pool do banco, em milissegundos. Reaproveita a chave por cliente do ADR 041. O teto do uvicorn segura o que passa pelo Nginx | Estado em memória do Nginx, por instância (hoje uma). O 503 do uvicorn acima do teto sai em `text/plain`, fora do contrato. Consumidores legítimos de um IP só (k6, prova de troca sem queda) precisam de ritmo |
| **B. Estender o limite por conta do ADR 028 a pedidos anônimos** | Um mecanismo só, que vale entre réplicas | Cada recusa custa uma escrita no banco, justamente o recurso que se quer proteger |
| **C. Só a proteção DDoS da Hostinger e da Cloudflare** | Nada a manter | Não enxerga abuso de baixo volume de um cliente, que já basta para esgotar um pool de 15 conexões |

**Log de erro:**

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. `error_log ... crit` no contexto http; diagnóstico pelo log de acesso** (escolhida) | Um mecanismo só, que cobre recusa, 413 e upstream. O log de acesso já trunca o IP e corta a query | Some o texto do erro de upstream (resolve, connect refused, too big header). O `crit` ainda grava IP e query em falta de recurso |
| **B. `limit_req_log_level info`, como na rodada 2 do #234** | Mantém o log de erro para o resto | Cobre só a recusa do `limit_req`. O 413 e o 502 continuam gravando IP e query |
| **C. Manter `notice` e tratar o log de erro como dado pessoal** | Diagnóstico completo | Contradiz a minimização do log de acesso e exige retenção e aviso próprios para um log que ninguém consulta no dia a dia |

## Decisão

**Limites (alternativa A):**

- Zonas por `$client_limit_key` (IPv4 inteiro, `/64` em IPv6, IPv4 mapeado contado como IPv4; ADR 041): `api` a 20 r/s e `readyz` a 5 r/s, ao lado da `auth` a 10 r/min (`docker/nginx/templates/default.conf.template:33-51`).
- `limit_req zone=api burst=40 nodelay` em `/api/` e na location de envio de conteúdo, que é por regex e por isso é escolhida antes do prefixo `/api/`; `limit_req zone=readyz burst=10 nodelay` em `/readyz` (`default.conf.template:163-185` e `:210-212`).
- A recusa é `429` problem+json no formato da API (`RATE_LIMITED`, `request_id`) com `Retry-After: 1`, pela location `@rate_limited` (`default.conf.template:199-206`).
- `/readyz` guarda o último resultado por 1 s (`ReadinessCache`, `src/goblinshift/entrypoints/api/health.py:25-48`), o que limita o banco a uma verificação por segundo por processo.
- uvicorn com `--limit-concurrency 30`, o dobro do pool do banco. Como ele compara com `>=` contando a conexão atual, atende até 29 conexões; a partir da 30ª responde `503` `text/plain`, sem `Retry-After` (`docker/app.Dockerfile:43-52`).
- **O 503 de plataforma fica fora do contrato OpenAPI**, documentado no plano (`docs/GoblinShiftPlan.md:1030`), sem `error_page` no Nginx (ruling, `frente-4-triagem.md:60`). A SPA já o trata como falha transitória: as leituras tentam de novo até 3 vezes (`web/src/api/queryRetry.ts`), e os comandos não repetem.
- **Parada graciosa de 8 s** (`--timeout-graceful-shutdown 8`): 8 s de pedidos em curso mais 2 s para o lifespan fechar o pool cabem na carência de 10 s.
- O k6 (`deploy/load/scenarios/limite-por-conta.js`) e a prova de troca sem queda (`scripts/check-zero-downtime.sh`) saem de um IP só e ganharam ritmo para caber no limite.

**Log de erro (alternativa A):** `error_log /var/log/nginx/error.log crit;` no contexto http (`default.conf.template:53-65`), por ruling da triagem (`frente-4-triagem.md:71`): IP é dado pessoal, e o log de acesso já foi desenhado para isso. Nesse contexto a diretiva substitui o `error_log` da imagem para as requisições; as mensagens do processo, sem dado de cliente, continuam no log da imagem. O `limit_req_log_level info` do #234 saiu, porque o `crit` já exclui a recusa. O log de acesso ganhou `upstream_connect_time`, que separa porta recusada (`-`) de conexão fechada depois de conectar (`docker/nginx/templates/access-log.conf.template`).

## Consequências

- Um cliente abusivo recebe 429 no Nginx sem tocar na API nem no banco. Uso legítimo fica longe do limite: a conta tem teto de 120 leituras por minuto na própria API, e a jornada do k6 mediu cerca de 10,7 req/s.
- O limite vale por instância do Nginx. Com mais de uma réplica, cada uma teria a sua cota. Hoje há uma.
- **Saturação da API vira 503 `text/plain`**, que um cliente de API vê fora do contrato. O alerta pelo log `Exceeded concurrency limit` ficou para o #236 e o #240, fora do lote de terça (`frente-4-triagem.md:99`).
- **A carência de 10 s é implícita.** Se alguém declarar `stop_grace_period` menor na `api`, o teste reprova; declarar 30 s explícito é decisão do dono, ainda pendente.
- **Diagnóstico pior no log de erro:** some o texto do erro de upstream (nome que não resolve, conexão recusada, `upstream sent too big header`). O status, o `upstream_status` e os tempos ficam no log de acesso. Dois pares de casos deixam de se distinguir: nome inexistente × resolver fora, e cabeçalho grande demais × conexão fechada.
- **O `crit` é raro, mas não limpo:** falta de recurso (disco cheio no `proxy_temp`, EMFILE, malloc) ainda grava `client:` e `request:` com IP completo e query, e nesse caso o log de acesso pode mostrar 200 para uma resposta cortada. `proxy_max_temp_file_size 0` tiraria o disco do caminho, mas muda o comportamento com respostas grandes; fica registrado como opção, não aplicada (`frente-4-triagem.md:82`).
- O aviso de privacidade e as notas internas descrevem o log de erro em `crit` (PR da frente 4p), sem mudar a versão do aviso.

## Compliance

- `tests/test_nginx_edge_limits.py` (Nginx real): rajada aceita e excesso recusado em `/api/` e `/readyz`; 429 problem+json com `Retry-After`; limite por cliente; envio de conteúdo contando na cota da API; `/readyz` sem gastar a cota da API; a recusa nas três zonas não grava IP nem query.
- `tests/test_nginx_logs.py`: 413 e 502 (conexão fechada, porta recusada, API fora do DNS) sem IP nem query em log nenhum, e o log de acesso com o status, o `upstream_status` e o `upstream_connect_time` de cada caso. Sabotagem `crit` → `notice` ou sem a diretiva: 9 testes reprovam.
- `tests/test_api_server_limits.py`: o teto do uvicorn é derivado do pool de `Settings`, e a parada graciosa termina antes do SIGKILL (lê a carência dos três arquivos de publicação, com padrão de 10 s, e exige `timeout <= carência - 2`).
- `tests/test_nginx_limit_consumers.py`: o ritmo do k6 e da prova de troca sem queda cabe no limite lido do template.
- `tests/test_health.py`: cache de 1 s do `/readyz`, com relógio injetado.
