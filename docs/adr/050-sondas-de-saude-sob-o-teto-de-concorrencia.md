# ADR 050. Sondas de saúde sob o teto de concorrência do uvicorn

- **Estado:** proposto (2026-11-10, revisto em três rodadas no PR #248). Decisão do dono: as alternativas que mudam algo atingem `deploy/coolify/app.json`, `docker/app.Dockerfile`, o template do Nginx, `deploy/coolify/release/receiver.py` ou a configuração administrativa do receptor de release, que são produção. Este ADR não altera código nem configuração.
- **Plano:** [3.7](../GoblinShiftPlan.md#s3-7) (sinais e alertas) e [3.8](../GoblinShiftPlan.md#s3-8) (deploy e VPS)
- **Relacionado:** ADR 048 (teto de 30 no uvicorn; o link entra quando o PR #243 for mesclado), [ADR 041](041-ip-do-cliente-atras-da-cloudflare.md) (caminho Traefik → Caddy → Nginx), [ADR 045](045-publicacao-em-producao-pelo-coolify.md) (publicação pelo Coolify), PR #240 (alerta `gs-causa-api-saturada`), `deploy/coolify/release/ISOLATION.md` (reconciliação de releases)

## Contexto

O lote de 10/11 pôs `--limit-concurrency 30` no uvicorn (`docker/app.Dockerfile:52`, ADR 048). O uvicorn 0.54.0 confere o teto a cada pedido, antes de chamar a aplicação. Se `len(connections) >= 30` ou `len(tasks) >= 30`, ele responde com `service_unavailable` (`uvicorn/protocols/http/httptools_impl.py:267-273` e `flow_control.py:42-54`, na tag 0.54.0). A rota não importa: `/livez` e `/readyz` são recusados como qualquer outro pedido.

A resposta do teto tem forma fixa e diferente da do `/readyz`:

| Origem do 503 | Corpo | Headers |
|---------------|-------|---------|
| Teto do uvicorn | `Service Unavailable` (19 bytes) | `content-type: text/plain; charset=utf-8`, `content-length: 19`, `connection: close` (mais `date`; sem `server`, por causa do `--no-server-header`) |
| `/readyz` sem banco ou schema | `{"status":"not_ready"}` | `content-type: application/json` (`src/goblinshift/entrypoints/api/health.py:77-83`) |

O Nginx repassa o 503 do upstream sem trocar o corpo: não há `error_page` para 503, por decisão do ADR 048.

As duas sondas do Compose passam pelo teto:

- **`api`** (`deploy/coolify/app.json:36-46`): `python -c "urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)"`, a cada 10 s, 5 tentativas, sem `start_period`.
- **`nginx`** (`deploy/coolify/app.json:270-284`): `curl --fail http://127.0.0.1/readyz`, que o Nginx repassa para `api:8000`. Também a cada 10 s, com 5 tentativas e `start_period` de 10 s.

**A sonda disputa o mesmo teto dos usuários.** O Docker executa o comando da sonda como um processo novo dentro do container. Esse processo não serve nada: ele abre uma conexão HTTP para a porta 8000, onde só existe um processo uvicorn (o CMD não tem `--workers`). A conexão entra no mesmo `server_state` dos pedidos dos usuários. A sonda do `nginx` chega ao mesmo processo pelo proxy. A frente 5f confirmou isso com o uvicorn 0.54.0 real: com o teto cheio, `/livez` respondeu 503 e voltou a 200 depois.

**Resultado:** com a API saturada por cerca de 50 s (5 falhas × 10 s), `api` e `nginx` ficam `unhealthy`. O `nginx` recém-criado leva uns 60 s (10 s de `start_period` + 5 × 10 s). A primeira sonda bem-sucedida devolve os dois a `healthy`.

## Quem age sobre um container `unhealthy`

A verificação usou quatro fontes: o código do Coolify 4.6.0 (tag `v4.6.0`, commit `0becfba`), o do Traefik v3.7.0, o do receptor de release do repositório e a documentação do Docker.

| Quem | Age sobre `unhealthy`? | Evidência |
|------|------------------------|-----------|
| **Receptor de release** (`deploy/coolify/release/receiver.py`), chamado por `.github/workflows/coolify.yml:170` → `deploy/coolify/release/send.sh` | **Sim: reprova a release.** Quando o Coolify termina, `deploy()` espera até 120 s enquanto cada container de `config["containers"]` estiver `starting` (`receiver.py:803-821`). `validate_runtime` levanta `Application container is not healthy` se a saúde não for `healthy` (`:479-485`). Depois, cada `config["readiness"]` recebe **um GET só, sem nova tentativa**, com timeout de 30 s, e precisa responder `200` (`:822-832`). Um 503 do teto nesse instante reprova a release. Na falha, nem o `pending.json` do projeto (gravado em `:795`, removido só no sucesso, em `:848`) nem o intent comum do host (`:921-924`, removido em `:935`) são apagados. `require_no_pending` (`:681-683`), chamado por `require_host_idle` (`:853-858`), passa a recusar **todas** as releases seguintes do host, de qualquer projeto, até a reconciliação manual de administrador (`deploy/coolify/release/ISOLATION.md`, "Reconciliação de falhas e estados pendentes") | Repositório. A lista de containers e de readiness está na configuração administrativa da VPS, fora do repositório. Pelo `image_roles` (`app`/`web`), os containers conferidos são a `api` e o `nginx` |
| **Docker Engine** (sem Swarm) | **Não.** `restart: unless-stopped` (`app.json:58` e `:300`) reinicia quando o container *sai*. A saúde só gera o evento `health_status` | Documentação do Docker, "Start containers automatically": as políticas falam em "exits". Só o Swarm substitui tarefa `unhealthy`, e o Coolify publica com `docker compose up -d` |
| **Coolify 4.6.0** | **Só mostra.** O status vira `running:unhealthy` ou `degraded:unhealthy` (`app/Services/ContainerStatusAggregator.php`). Não reinicia container de aplicação por saúde: o único `StopApplication` depende de `restartLimitReached`, que conta `RestartCount` do Docker (`app/Actions/Docker/GetContainersStatus.php:530-549`). Os reinícios automáticos são do `coolify-proxy`, do Sentinel e do proxy TCP de banco (`app/Jobs/PushServerUpdateJob.php:832` e `:869`, `app/Jobs/ServerCheckJob.php:94`, `app/Jobs/CheckAndStartSentinelJob.php:61`). Não notifica: `StatusChanged` só é instanciado pelo comando de teste de e-mail (`app/Console/Commands/Emails.php:154`) | Código da tag `v4.6.0` |
| **Coolify depois da 4.6.0** | **Depende da versão.** `app/Jobs/NotifyResourceStoppedJob.php` já existe na `v4.7.3` e no `main` (último commit `f21acdc`, 2026-11-03) e notifica `StatusChanged`. Hoje só filtra `exited%`, não `unhealthy`. Uma atualização do Coolify pode passar a notificar ou agir sobre a saúde | Código do `main` e da tag `v4.7.3` |
| **Deploy pelo Coolify** | **Não espera saúde.** No build pack Docker Compose, `deploy_docker_compose_buildpack` para os containers (`stop_running_container(force: true)`, `ApplicationDeploymentJob.php:895`) e roda `up -d` sem `--wait`, sem o `health_check()` do rolling update dos outros build packs. Quem espera é o próprio Compose: o `nginx` só é criado depois de a `api` ficar `healthy` (`app.json:262-263`). Isso vale na criação, não durante a operação | Código da tag `v4.6.0`; documentação do Compose, `depends_on` |
| **Traefik do Coolify** | **Agiria, mas não vê a `api` nem o `nginx`.** O provider Docker descarta container com saúde diferente de `healthy` (`pkg/provider/docker/config.go:228-229` no Traefik v3.7.0; o Coolify 4.6.0 usa `traefik:v3.7`). Só que `api` e `nginx` têm `traefik.enable=false` (`app.json:94` e `:304`). O Traefik roteia para o gateway Caddy (`deploy/coolify/gateway.json`), cuja sonda consulta só a API de administração do próprio Caddy | Código do Traefik; `gateway.json` |
| **Caddy (gateway)** | **Não.** `reverse_proxy 172.31.0.4:80` com um upstream fixo, sem `health_uri` nem `fail_duration`. Só repete a conexão por 5 s (`docker/gateway/sites/goblinshift.dev.caddy:16-20`) | Repositório |
| **Nginx da app** | **Provavelmente não; não verificado.** Resolve `api` pelo DNS embutido do Docker (`resolver 127.0.0.11`, `set $api http://api:8000`). Fora do Swarm, esse DNS não deveria filtrar por saúde, mas não conferi isso nesta versão do Docker. O impacto seria limitado: com um upstream só, `/api` passaria de 503 a 502, a SPA continuaria no ar e a carga cairia, o que daria oscilação, não queda total | Repositório; comportamento do DNS não verificado |
| **Monitor de uptime externo** | **Avisa** por conta própria depois de duas falhas seguidas a cada 2 min (`deploy/uptime/README.md:3-5`). A consulta passa pelo teto, então ele avisa uma saturação de uns 4 min. O aviso é verdadeiro | Repositório |

**Conclusão:** há dois efeitos, e é preciso separá-los.

1. **O site não cai.** Nenhum proxy tira a `api` ou o `nginx` do roteamento por saúde. Os pedidos acima do teto recebem 503 do uvicorn, e a SPA estática continua servida pelo Nginx.
2. **A release pode cair e travar a fila.** O Coolify para tudo antes do `up -d`, então a `api` nova nasce sem carga e o `depends_on` não quebra por saturação. Mas o tráfego reprimido volta assim que o `nginx` sobe. Quem decide é a readiness: se p é a fração do tempo em que o teto está cheio, o GET único falha com probabilidade de cerca de p. O `nginx` só chega a `unhealthy` se as cerca de 6 sondas em 60 s falharem todas (cerca de p⁶). Sob saturação sustentada, o receptor reprova uma release que está, na verdade, rodando. O `pending.json` fica, e nenhuma release de nenhum projeto do host entra até um administrador reconciliar à mão. Isso pesa justamente quando mais se quer publicar uma correção: com a API sob carga.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Aceitar o `unhealthy` como sinal de saturação, travar por teste as premissas que o tornam inofensivo para o tráfego e aceitar por escrito o risco na release** | Nenhuma mudança em produção. O sinal é verdadeiro: com o teto cheio, os usuários recebem 503. O teto do ADR 048 continua protegendo o pool | **Não trata o efeito 2:** a release sob saturação longa é reprovada e trava a fila do host até a reconciliação manual. O status do Coolify fica ambíguo entre saturação e banco fora. Se o Coolify passar a notificar ou agir sobre saúde, o ADR precisa ser refeito |
| **B. Readiness interna fora do teto e fora do pool, no mesmo processo.** Um segundo listener (por exemplo um segundo `uvicorn.Server` em `:8001` no mesmo laço de eventos, só na rede `internal`, sem rota no Nginx) serve `/livez` e um `/readyz` que **não usa o pool da app**: conexão própria com `connect_timeout` curto, ou um pool dedicado de 1, e timeout total menor que o da sonda (2 s). A sonda da `api` e a readiness do receptor apontam para ele; a do `nginx` vira E | Trata os dois efeitos **quando o banco responde**. Com o teto e o pool da app cheios, a release passa se o banco aceitar uma conexão nova e responder dentro do timeout. A prova de banco e schema continua valendo, e o laço travado falha nas duas portas. Fica protegida contra um Coolify futuro que notifique ou aja por saúde | É a mais cara. Exige entrypoint próprio no lugar do CMD do uvicorn, sinais e parada graciosa (8 s + 2 s do ADR 048) coordenados entre dois servidores, e testes de processo novos. Precisa de uma conexão de banco fora do pool a cada sonda, o mesmo custo que pesa contra D, contido por um cache de 1 s e pelo `max_connections` do PostgreSQL. Muda o Dockerfile, o `app.json`, o template do Nginx (por E) e a configuração administrativa do receptor, que está fora do repositório. **Com o mesmo `ReadinessCache` e o pool da app, B não serve:** o `/readyz` faz `engine.connect()` no pool de 10 + 5 (`src/goblinshift/entrypoints/api/health.py:56`), com `pool_timeout` de 5 s (`src/goblinshift/platform/settings.py:88-90`); saturação no teto costuma vir com o pool esgotado, e a resposta seria 503 JSON depois que a sonda já desistiu |
| **B'. Outro processo para `/livez`** (um segundo uvicorn no container) | Simples de escrever | Não serve: um processo à parte responde mesmo com o processo principal travado |
| **C. Aumentar o teto** | Uma linha no Dockerfile | Desfaz o motivo do ADR 048: 30 já é o dobro do pool (10 + 5). Acima disso, os pedidos esperam conexão em vez de receber 503 rápido. Só empurra o limiar |
| **C'. Aumentar `interval` ou `retries`** (por exemplo 10 s × 18, cerca de 3 min) | Só `app.json`. Absorve picos curtos no painel | Não muda a release: o que decide é a readiness, que já falha com probabilidade de cerca de p. Saturação longa continua dando `unhealthy`. Atrasa a detecção de banco fora e piora o deploy com o banco fora (ver Consequências) |
| **D. Sonda da `api` sem HTTP**, no molde de `worker` e `maintenance` (`app.json:110-122`): processo em `/proc/1/cmdline` e `SELECT 1` direto | Fora do teto e do pool, sem código na app. Consistente com os outros serviços | Uma conexão de banco fora do pool a cada 10 s, mesmo custo de B, justamente com o banco lento. Deixa de provar que o HTTP responde. Não trata a sonda do `nginx` nem a readiness do receptor, que é HTTP. Sozinha, não muda a release |
| **E. Sonda do `nginx` só sobre o próprio Nginx** (`location = /nginx-health { allow 127.0.0.1; deny all; return 200; }`) | O status do `nginx` passa a dizer se ele serve a SPA. Barata | Muda o template e o `app.json`. Não muda a release, pelo mesmo motivo de C'. Não trata a `api` |
| **F. A sonda trata o 503 do teto como "vivo e saturado"**: sai com 0 em `200`, ou em `503` com `content-type: text/plain; charset=utf-8` e corpo `Service Unavailable`; sai com 1 em qualquer outra coisa | Barata: só `app.json`. O 503 do teto só vem do uvicorn. Na `api`, todo 503 da app sai como problem+json (`problems.py:238-241`), e o 503 do próprio Nginx é HTML | **Não muda o resultado da release:** tira dos containers uma falha de cerca de p⁶, enquanto a readiness continua falhando com cerca de p. **Mostra verde com o teto cheio por tempo indeterminado:** o envio de conteúdo usa `proxy_request_buffering off` e `proxy_read_timeout 600s` (`default.conf.template:210-216`), então 30 envios lentos, dentro do burst de 40, seguram o teto por até 10 minutos com a `api` viva para o laço e morta para os usuários. Troca um sinal verdadeiro por um falso. Depende da forma exata do 503 do uvicorn 0.54.0. Na sonda do `nginx`, exige `CMD-SHELL` com `curl -w` e comparação no shell |
| **G. O receptor repete o GET da readiness até receber 200, dentro de um prazo** (por exemplo 120 s com intervalo de 2 s, como já faz com os containers `starting`) | Bem mais barata que B: algumas linhas em `receiver.py`, sem mudar a app. **Não enfraquece o portão:** só aprova com 200, ou seja, com banco e schema provados. Resolve a saturação intermitente, em que algum GET passa dentro do prazo | Muda `receiver.py`, infraestrutura revisada e compartilhada entre os projetos do host (`ISOLATION.md`), com reinstalação coordenada. Não resolve saturação contínua por mais que o prazo: nesse caso a release ainda é reprovada. Alonga a espera de uma release que vai falhar por outro motivo |
| **G'. O receptor aceita o 503 do teto como aprovação** | Release passa sob qualquer saturação | **Enfraquece o portão:** aprova sem provar banco nem schema. Não recomendada |

## Decisão (proposta)

**Recomendação: A agora, com o risco na release aceito por escrito.** Quando publicar sob carga virar requisito, o próximo passo é G (repetir o GET até 200 no receptor), que é barata e cobre a saturação intermitente. Se for preciso cobrir saturação contínua, o passo é B na forma corrigida, com a readiness interna fora do pool.

- **Risco aceito:** uma release feita com a API saturada é reprovada pela readiness do receptor com probabilidade de cerca de p (a fração do tempo com o teto cheio). O `pending.json` do projeto e o intent do host ficam, e nenhuma release de nenhum projeto do host entra até um administrador reconciliar pelo `ISOLATION.md`, "Reconciliação de falhas e estados pendentes":
  1. congelar ingressos sob `host.lock`;
  2. conferir no Coolify se o deployment terminou com a versão nova rodando;
  3. preservar o recibo no histórico;
  4. arquivar os intents por rename.

  Antes de publicar durante saturação, o dono pode reduzir a carga primeiro.
- **F sai da recomendação.** Ela não muda o resultado da release e troca um sinal verdadeiro por um verde falso.
- **Não recomendadas:**
  - C desfaz o ADR 048;
  - C' e E não mudam a release;
  - D perde a prova de HTTP e também não muda a release;
  - G' enfraquece o portão.

**Bottom line:** A, porque nada hoje derruba o site por saúde, e a única consequência real, a release reprovada sob saturação, depende da readiness do receptor. Nenhuma mudança barata nas sondas de container altera isso. O custo é aceitar que publicar durante saturação pode falhar e travar a fila do host até uma reconciliação manual. Se isso virar inaceitável, G resolve o caso intermitente com algumas linhas no receptor, e só B com conexão fora do pool resolve o contínuo.

## Consequências

- **Tráfego:** sob saturação, o site não cai. O painel do Coolify mostra a `api` e o `nginx` como `unhealthy`, o que é verdadeiro. Para distinguir de banco fora, o lugar de olhar é o alerta `gs-causa-api-saturada` (#240) e o log da API. Isso deve constar em `deploy/coolify/README.md`.
- **Release:** sob saturação, o receptor reprova a release, principalmente pela readiness. O `pending.json` fica e bloqueia releases de todos os projetos do host até a reconciliação manual (`ISOLATION.md`). A versão nova já está rodando; falta o registro.
- **Deploy com o banco fora** (risco anterior a este ADR, fora da saturação): se a `api` nova nunca ficar `healthy`, o `up -d` falha, os containers antigos já foram removidos e o `nginx` não é criado. Isso é queda total. A não muda esse caso, e C' o pioraria, ao atrasar a detecção.
- **Dependência da versão do Coolify:** a premissa de que o Coolify não age nem notifica por saúde vale na 4.6.0. A `v4.7.3` já tem `NotifyResourceStoppedJob`, por enquanto só para `exited`. A versão do Coolify não está no repositório, então a checagem fica no roteiro de atualização.
- A lacuna L2 da frente 5f se divide: a hipótese de que sobrecarga parcial vira queda total não vale na topologia atual, e o efeito real é a release reprovada com o host travado.

## Compliance

Testes e verificações que provam a escolha A (a escrever num PR seguinte):

1. **Processo, sem Docker** (`tests/test_api_probe_under_limit.py`): sobe o uvicorn real com as flags lidas do CMD de `docker/app.Dockerfile` e um app com uma rota lenta, e ocupa `N - 1` vagas. Afirma que o comando de sonda da `api`, lido de `app.json`, sai com código diferente de zero enquanto o teto está cheio e com zero depois que os pedidos terminam. Isso escreve como teste o comportamento aceito: quem mudar a sonda ou o teto precisa revisitar este ADR.
2. **Premissas do tráfego** (`tests/test_probe_consumers.py`): falha se acontecer qualquer uma destas mudanças:
   - `api` ou `nginx` em `app.json` deixam de ter `traefik.enable=false`;
   - algum serviço usa imagem ou rótulo de `autoheal`;
   - os trechos de `docker/gateway/sites/*.caddy` ganham `health_uri`, `health_port` ou `fail_duration`;
   - `restart` da `api` e do `nginx` deixa de ser uma política baseada em saída.

   Sabotagem esperada: tirar `traefik.enable=false` do `nginx` reprova o teste.
3. **Premissa da release** (no mesmo arquivo): `receiver.py` exige `healthy` em `validate_runtime` e `200` num GET único de readiness. Se alguém adotar G ou G', o teste falha e obriga a atualizar este ADR.
4. **Atualização do Coolify:** item no roteiro de atualização (`deploy/coolify/README.md`), porque a versão não está no repositório. Antes de atualizar, conferir se `NotifyResourceStoppedJob` ou outro job passou a agir ou notificar sobre `unhealthy`, e revisitar este ADR se sim.

Se o dono escolher G, o teste da release é unitário em `deploy/coolify/release/test_receiver.py`: readiness com 503, 503 e 200 dentro do prazo aprova; só 503 até o prazo reprova; 503 `text/plain` nunca aprova sozinho. Se escolher B, o teste 1 passa a exigir 200 na porta interna com o teto principal e o pool da app cheios.
