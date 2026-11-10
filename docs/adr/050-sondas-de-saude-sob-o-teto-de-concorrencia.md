# ADR 050. Sondas de saúde sob o teto de concorrência do uvicorn

- **Estado:** proposto (2026-11-10, revisto na rodada 1 do PR #248). Decisão do dono: as alternativas que mudam algo atingem `deploy/coolify/app.json`, `docker/app.Dockerfile`, o template do Nginx ou a configuração administrativa do receptor de release, que são produção. Este ADR não altera código nem configuração.
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
| **Receptor de release** (`deploy/coolify/release/receiver.py`), chamado por `.github/workflows/coolify.yml:170` → `deploy/coolify/release/send.sh` | **Sim: reprova a release.** Quando o Coolify termina, `deploy()` espera até 120 s enquanto cada container de `config["containers"]` estiver `starting` (`receiver.py:803-821`). `validate_runtime` levanta `Application container is not healthy` se a saúde não for `healthy` (`:479-485`). Depois, cada `config["readiness"]` precisa responder `200` a um `GET` (`:822-832`); um 503 do teto reprova também. Na falha, nem o `pending.json` do projeto (gravado em `:795`, removido só no sucesso, em `:848`) nem o intent comum do host (`:921-924`, removido em `:935`) são apagados. `require_no_pending` (`:681-683`), chamado por `require_host_idle` (`:853-858`), passa a recusar **todas** as releases seguintes do host, de qualquer projeto, até a reconciliação manual de administrador (`deploy/coolify/release/ISOLATION.md`, "Reconciliação de falhas e estados pendentes") | Repositório. A lista de containers e de readiness está na configuração administrativa da VPS, fora do repositório. Pelo `image_roles` (`app`/`web`), os containers conferidos são a `api` e o `nginx` |
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
2. **A release pode cair e travar a fila.** O Coolify para tudo antes do `up -d`, então a `api` nova nasce sem carga e o `depends_on` não quebra por saturação. Mas o tráfego reprimido volta assim que o `nginx` sobe. Sob saturação sustentada, o receptor encontra o `nginx` `unhealthy` dentro da janela de 120 s, ou recebe 503 na readiness HTTP, e reprova uma release que está, na verdade, rodando. O `pending.json` fica, e nenhuma release de nenhum projeto do host entra até um administrador reconciliar à mão. Isso pesa justamente quando mais se quer publicar uma correção: com a API sob carga.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Aceitar o `unhealthy` como sinal de saturação e travar por teste as premissas que o tornam inofensivo para o tráfego** | Nenhuma mudança em produção. O sinal é verdadeiro. O teto do ADR 048 continua protegendo o pool | **Não trata o efeito 2:** release sob saturação sustentada é reprovada e trava a fila do host até reconciliação manual. O status do Coolify fica ambíguo entre saturação e banco fora. Se o Coolify passar a notificar ou agir sobre saúde, o ADR precisa ser refeito |
| **B. Sondas e readiness da release fora do teto, no mesmo processo**: um segundo listener interno (por exemplo um segundo `uvicorn.Server` em `:8001` no mesmo laço de eventos, só na rede `internal`, sem rota no Nginx) servindo `/livez` e `/readyz` com o mesmo `ReadinessCache`. A sonda da `api` e a readiness do receptor apontam para ele; a do `nginx` vira E | **Trata os dois efeitos.** A release passa sob saturação sem perder a prova de banco e schema. A sonda continua provando que o laço de eventos do processo principal responde (um laço travado falha nas duas portas). Fica protegida contra Coolify futuro que notifique ou aja por saúde. Mantém o teto para os usuários | É a mais cara: entrypoint próprio no lugar do CMD do uvicorn; sinais e parada graciosa (8 s + 2 s do ADR 048) coordenados entre dois servidores; testes de processo novos. Muda o Dockerfile, o `app.json`, o template do Nginx (por E) e a configuração administrativa do receptor, que está fora do repositório. Uma porta sem teto é caminho de carga no banco, limitado pelo cache de 1 s e por estar só na rede interna |
| **B'. Outro processo para `/livez`** (segundo uvicorn no container) | Simples de escrever | Não serve: um processo à parte responde mesmo com o processo principal travado |
| **C. Aumentar o teto** | Uma linha no Dockerfile | Desfaz o motivo do ADR 048: 30 já é o dobro do pool (10 + 5); acima disso os pedidos esperam conexão em vez de receber 503 rápido. Só empurra o limiar |
| **C'. Aumentar `interval` ou `retries`** (por exemplo 10 s × 18, cerca de 3 min) | Só `app.json`. Absorve picos curtos e reduz o falso negativo do receptor nos containers: com 18 tentativas, o `nginx` não chega a `unhealthy` na janela de 120 s | Não trata a readiness HTTP do receptor, que recebe 503 na hora. Saturação longa continua dando `unhealthy`. Atrasa a detecção de banco fora no painel e piora o deploy com banco fora (ver Consequências) |
| **D. Sonda da `api` sem HTTP**, no molde de `worker` e `maintenance` (`app.json:110-122`): processo em `/proc/1/cmdline` e `SELECT 1` direto | Fora do teto, sem código na app. Consistente com os outros serviços | Uma conexão de banco fora do pool a cada 10 s, justamente com o banco lento. Deixa de provar que o HTTP responde. Não trata a sonda do `nginx` nem a readiness do receptor |
| **E. Sonda do `nginx` só sobre o próprio Nginx** (`location = /nginx-health { allow 127.0.0.1; deny all; return 200; }`) | O status do `nginx` passa a dizer se ele serve a SPA. Barata. Tira o `nginx` do falso negativo do receptor | Muda o template e o `app.json`. Sozinha, não trata a `api` nem a readiness HTTP |
| **F. A sonda trata o 503 do teto como "vivo e saturado"**: sai com 0 em `200`, ou em `503` com `content-type: text/plain; charset=utf-8` e corpo `Service Unavailable`; sai com 1 em qualquer outra coisa (503 JSON do `/readyz`, timeout, conexão recusada, 502 do Nginx). Na `api`, o `python -c` lê status, header e corpo; no `nginx`, `curl -s -o - -w '%{http_code} %{content_type}'` em vez de `--fail` | **Barata:** só `app.json`, sem código nem entrypoint. Continua provando que o laço de eventos responde, porque o 503 do teto é escrito por ele, e que o banco está pronto quando um pedido passa. Tira `api` e `nginx` do falso negativo do receptor nos containers. Protege contra Coolify futuro que notifique ou aja por saúde | **Não trata a readiness HTTP do receptor**, que exige 200 e recebe o 503 do teto. Depende da forma exata do 503 do uvicorn, que precisa de teste com o uvicorn real para pegar uma mudança de versão. Esconde a saturação no painel do Coolify, o que é aceitável porque o alerta do #240 e o monitor de uptime cobrem esse sinal. Sob saturação total, o `/readyz` nunca passa, então banco fora e saturação ao mesmo tempo ficam como "vivo" até um pedido passar |

**Variação para a readiness do receptor** (vale para A, C', E e F): o receptor poderia aceitar o 503 do teto como inconclusivo e repetir até um prazo. Isso exige mudar `receiver.py`, que é infraestrutura revisada e compartilhada entre projetos (`ISOLATION.md`). Também enfraquece o portão: sob saturação sustentada, a release passaria sem provar banco e schema. Fica registrada, não recomendada.

## Decisão (proposta)

**Recomendação: F nas duas sondas de container agora, e B como próximo passo se o dono quiser que a release passe sob saturação sustentada.**

- **F** é uma mudança só no `app.json`. Ela resolve o painel, a metade "containers" do portão do receptor e a exposição a um Coolify futuro que aja ou notifique por saúde. Preserva a prova de laço vivo e de banco pronto.
- **A readiness HTTP do receptor continua exigindo 200.** Uma release sob saturação sustentada ainda é reprovada nesse passo e trava a fila do host. Esse risco fica **aceito explicitamente**, com a reconciliação de `ISOLATION.md`: o administrador confere se o deployment terminou com a versão nova rodando, preserva o recibo no histórico e arquiva os intents por rename. Antes de publicar uma correção durante saturação, o dono pode reduzir a carga primeiro.
- **B** passa a ser a escolha se uma release for travada por saturação, ou se o dono decidir agora que publicar sob carga é requisito. B é o único caminho que trata os dois efeitos sem enfraquecer o portão.
- **Não recomendadas:** C desfaz o ADR 048. C' só atrasa. D perde a prova de HTTP. A sozinha deixa o efeito 2 inteiro.

**Bottom line:** F, porque com uma mudança de configuração, sem código, as sondas de container deixam de confundir "saturado" com "morto", o que hoje reprovaria a release e amanhã poderia disparar notificação ou reinício num Coolify mais novo. O custo principal é aceitar que a release sob saturação sustentada continua sendo reprovada pela readiness HTTP do receptor e trava a fila do host até reconciliação manual. Resolver isso de vez exige B: entrypoint próprio, porta interna e mudança na configuração administrativa do receptor.

## Consequências

- **Tráfego:** sob saturação, o site não cai. Com F, o painel do Coolify mostra a `api` e o `nginx` como `healthy` enquanto o laço responde. A saturação aparece no alerta `gs-causa-api-saturada` (#240) e no monitor de uptime, que deve constar em `deploy/coolify/README.md` como o lugar onde olhar.
- **Release:** sob saturação sustentada, o receptor reprova a release na readiness HTTP. O `pending.json` fica e bloqueia releases de todos os projetos do host até a reconciliação manual (`ISOLATION.md`). A versão nova já está rodando; o que falta é o registro. Sem F, a reprovação também viria pelos containers `unhealthy`.
- **Deploy com o banco fora (risco anterior a este ADR, fora da saturação):** se a `api` nova nunca ficar `healthy`, o `up -d` falha, os containers antigos já foram removidos e o `nginx` não é criado. Isso é queda total. F não muda esse caso, porque o 503 JSON continua sendo falha. C' o pioraria, ao atrasar a detecção.
- **Dependência da versão do Coolify:** a premissa "o Coolify não age nem notifica por saúde" vale na 4.6.0. A `v4.7.3` já tem `NotifyResourceStoppedJob`, por enquanto só para `exited`. A versão do Coolify não está no repositório, então nenhum teste daqui detecta a atualização. A checagem fica como item obrigatório do roteiro de atualização do Coolify: reler este ADR e o filtro do `NotifyResourceStoppedJob` antes de atualizar.
- **Dependência da versão do uvicorn:** F depende do corpo e dos headers do 503 do teto (uvicorn 0.54.0). O teste 1 abaixo reprova se a forma mudar numa atualização.
- A lacuna L2 da frente 5f se divide: a hipótese "sobrecarga parcial vira queda total" não vale na topologia atual, e "release reprovada e host travado" é o efeito real.

## Compliance

Testes que provariam a escolha F, a escrever no PR que a implementar:

1. **Processo, sem Docker** (`tests/test_api_probe_under_limit.py`). Sobe o uvicorn real com as flags lidas do CMD de `docker/app.Dockerfile` e um app ASGI com uma rota lenta e um `/readyz` controlável. Afirma que o comando de sonda da `api`, lido de `app.json`:
   - sai com **0** com `N - 1` pedidos lentos em curso (503 do teto);
   - sai com **0** com o teto livre e `/readyz` pronto;
   - sai com **1** com `/readyz` em 503 JSON;
   - sai com **1** sem nada escutando na porta.

   O mesmo teste afirma a forma do 503 do teto (status, `content-type`, corpo) para pegar uma mudança do uvicorn. Com a sonda de hoje, o primeiro caso falha, e isso prova que o teste distingue.
2. **Sonda do `nginx`** (`tests/test_nginx_probe.py`, com o Nginx real, como `test_nginx_edge_limits.py`). Com um upstream falso que devolve o 503 do teto, a sonda lida de `app.json` sai com 0. Com um upstream que devolve 503 JSON, ou com o upstream fora (502), sai com 1.
3. **Configuração** (`tests/test_probe_consumers.py`). Falha em qualquer um destes casos:
   - `api` ou `nginx` em `app.json` deixam de ter `traefik.enable=false`;
   - algum serviço usa imagem ou rótulo de `autoheal`;
   - `docker/gateway/sites/*.caddy` ganha `health_uri`, `health_port` ou `fail_duration`;
   - `restart` da `api` e do `nginx` deixa de ser uma política baseada em saída;
   - `receiver.py` deixa de exigir `healthy` em `validate_runtime` ou `200` na readiness. Nesse caso o ADR precisa ser revisto nos dois sentidos.
4. **Atualização do Coolify:** item no roteiro de atualização (`deploy/coolify/README.md`), porque a versão não está no repositório: antes de atualizar, conferir se `NotifyResourceStoppedJob` ou outro job passou a agir ou notificar sobre `unhealthy`, e revisitar este ADR se sim.

Se o dono escolher B, o teste 1 passa a exigir 200 na porta interna com o teto principal cheio, e um teste do receptor confere a readiness apontando para ela.
