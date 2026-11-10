# ADR 050. Sondas de saúde sob o teto de concorrência do uvicorn

- **Estado:** proposto (2026-11-10). Decisão do dono: as alternativas que mudam algo atingem `deploy/coolify/app.json`, `compose.yaml` ou `docker/app.Dockerfile`, que são configuração de produção. Este ADR não altera código nem configuração.
- **Plano:** [3.7](../GoblinShiftPlan.md#s3-7) (sinais e alertas) e [3.8](../GoblinShiftPlan.md#s3-8) (deploy e VPS)
- **Relacionado:** ADR 048 (teto de 30 no uvicorn, em revisão no PR #243), [ADR 041](041-ip-do-cliente-atras-da-cloudflare.md) (caminho Traefik → Caddy → Nginx), [ADR 045](045-publicacao-em-producao-pelo-coolify.md) (publicação pelo Coolify), PR #240 (alerta `gs-causa-api-saturada`)

## Contexto

O lote de 10/11 pôs `--limit-concurrency 30` no uvicorn (`docker/app.Dockerfile:52`, ADR 048). O uvicorn 0.54.0 confere o teto a cada pedido, antes de chamar a aplicação: se `len(connections) >= 30` ou `len(tasks) >= 30`, responde `503` `text/plain` (`uvicorn/protocols/http/httptools_impl.py:267-273` na tag 0.54.0). A rota não importa. `/livez` e `/readyz` são recusados como qualquer outro pedido.

As duas sondas do Compose passam por esse teto:

- **`api`** (`deploy/coolify/app.json:36-46`): `python -c "urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2)"`, a cada 10 s, 5 tentativas, sem `start_period`.
- **`nginx`** (`deploy/coolify/app.json:270-284`): `curl --fail http://127.0.0.1/readyz`, que o Nginx repassa para `api:8000`, a cada 10 s, 5 tentativas.

**A sonda roda no mesmo processo uvicorn? Sim, quanto ao teto.** O Docker executa o comando da sonda como um processo novo dentro do container. Esse processo não serve nada: ele abre uma conexão HTTP para a porta 8000, onde só existe um processo uvicorn (o CMD não tem `--workers`). A conexão entra no mesmo `server_state` dos pedidos dos usuários e disputa as mesmas 29 vagas. A sonda do `nginx` chega ao mesmo processo pelo proxy. A frente 5f confirmou com o uvicorn 0.54.0 real: com o teto cheio, `/livez` respondeu 503 e voltou a 200 depois.

**Resultado:** com a API saturada por cerca de 50 s (5 falhas × 10 s), `api` e `nginx` ficam `unhealthy` ao mesmo tempo. A primeira sonda bem-sucedida os devolve a `healthy`.

## O que acontece de fato com um container `unhealthy` nesta topologia

A lacuna L2 da frente 5f levantou a hipótese de o proxy tirar o container do roteamento e transformar sobrecarga parcial em queda total. A verificação abaixo, feita no código do Coolify 4.6.0 (tag `v4.6.0`, commit `0becfba`), no Traefik e na documentação do Docker, mostra que hoje isso não acontece:

| Quem | Age sobre `unhealthy`? | Evidência |
|------|------------------------|-----------|
| **Docker Engine** (sem Swarm) | **Não.** `restart: unless-stopped` reinicia quando o container *sai*. A saúde só gera o evento `health_status` e aparece no `docker ps` | Docs do Docker, "Start containers automatically": as políticas falam em "exits"; nenhuma menciona saúde. Só o Swarm substitui tarefa `unhealthy`, e o Coolify publica com `docker compose up -d` |
| **Coolify 4.6.0** | **Só mostra.** O status do recurso vira `running:unhealthy` ou `degraded:unhealthy` (`app/Services/ContainerStatusAggregator.php`). Não reinicia container de aplicação: os reinícios automáticos do código são do `coolify-proxy`, do Sentinel e do proxy TCP de banco (`app/Jobs/PushServerUpdateJob.php:832` e `:869`, `app/Jobs/ServerCheckJob.php:94`, `app/Jobs/CheckAndStartSentinelJob.php:61`). Não notifica: `StatusChanged` só é instanciado pelo comando de teste de e-mail (`app/Console/Commands/Emails.php:154`). `RestartLimitReached` conta reinícios do Docker, que não acontecem por saúde | Código da tag `v4.6.0` |
| **Deploy pelo Coolify** | **Não espera saúde** no build pack Docker Compose: `deploy_docker_compose_buildpack` para os containers e roda `up -d`, sem o `health_check()` do rolling update dos outros build packs (`app/Jobs/ApplicationDeploymentJob.php:780` e `:895`). Quem espera é o próprio Compose: o `nginx` só é criado depois de a `api` ficar `healthy` (`depends_on: condition: service_healthy`, `app.json:262-263`). Isso vale na criação, não durante a operação | Código da tag `v4.6.0`; docs do Compose, `depends_on` |
| **Traefik do Coolify** | **Agiria, mas não vê a `api` nem o `nginx`.** O provider Docker descarta container com saúde diferente de `healthy` (`pkg/provider/docker/config.go:228-229` no Traefik v3.7.0; o Coolify 4.6.0 usa `traefik:v3.7`). Só que `api` e `nginx` têm `traefik.enable=false` (`app.json:94` e `:304`). O Traefik roteia para o gateway Caddy (`deploy/coolify/gateway.json`), cuja sonda consulta só a API de administração do próprio Caddy | Código do Traefik; `gateway.json` |
| **Caddy (gateway)** | **Não.** `reverse_proxy 172.31.0.4:80` com um upstream fixo, sem `health_uri` nem `fail_duration`, ou seja, sem verificação ativa nem passiva. Só repete a conexão por 5 s (`docker/gateway/sites/goblinshift.dev.caddy:16-20`) | Repositório |
| **Nginx da app** | **Não.** Resolve `api` pelo DNS embutido do Docker (`resolver 127.0.0.11`); fora do Swarm esse DNS devolve o container sem olhar a saúde | Repositório; comportamento do DNS embutido conhecido, não conferido nesta versão do Docker |
| **Monitor de uptime externo** | **Avisa**, por conta própria: consulta `https://goblinshift.dev/readyz` a cada 2 min e avisa no Discord depois de duas falhas seguidas (`deploy/uptime/README.md`). Essa consulta também passa pelo teto, então avisa sob saturação que dure uns 4 min | Repositório |

Conclusão: hoje `unhealthy` na `api` e no `nginx` é só **estado exibido no Coolify**. O tráfego continua chegando: os pedidos acima do teto recebem 503 do uvicorn, e a SPA estática continua servida pelo Nginx. Não há queda total. O risco é latente: ele aparece se alguém passar a agir sobre a saúde, por exemplo com rótulos do Traefik no `nginx`, com um container `autoheal`, com Swarm ou com verificação de saúde no Caddy.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Aceitar o `unhealthy` como sinal de que a API não está atendendo, e travar por teste as premissas que o tornam inofensivo** (recomendada) | Nenhuma mudança em compose, Dockerfile ou produção. É verdade: sob saturação, pedidos novos recebem 503, e `unhealthy` diz isso. O teto do ADR 048 continua protegendo o pool. Um teste de configuração falha no dia em que alguém der ao Traefik ou ao Caddy o poder de agir sobre a saúde | O status do Coolify fica ambíguo: `unhealthy` pode ser banco fora, schema incompatível ou saturação. A causa vem do log da API e do alerta do PR #240. O Coolify não notifica, então o sinal só vale para quem olha o painel. Se uma premissa mudar, a decisão precisa ser refeita |
| **B. Servir `/livez` fora do teto, em outra porta no mesmo processo** (um segundo `uvicorn.Server` em `127.0.0.1:8001` no mesmo laço de eventos, ou um respondedor `asyncio` mínimo aberto no lifespan) e apontar a sonda da `api` para ela | A sonda passa a provar que o laço de eventos responde, que é o sentido de *liveness*, sem disputar vaga com usuários. Mantém o teto | Troca o CMD do uvicorn por um entrypoint próprio, ou acrescenta servidor ao lifespan: sinais, parada graciosa (8 s + 2 s do ADR 048) e testes de processo novos. Deixa de provar o banco e o schema, que o `/readyz` prova hoje. A sonda do `nginx` não alcança `127.0.0.1` da `api` e precisa de outro alvo. É código para proteger contra uma ação que hoje ninguém toma |
| **B'. Outro processo para `/livez`** (segundo uvicorn no container) | Simples de escrever | Não serve: um processo à parte responde mesmo com o processo principal travado, então a sonda deixa de medir o que importa |
| **C. Aumentar o teto** | Uma linha no Dockerfile | Desfaz o motivo do ADR 048: com 30 o teto já é o dobro do pool (10 + 5); acima disso os pedidos esperam conexão em vez de receber 503 rápido. Só empurra o limiar, não separa sonda de usuário |
| **C'. Aumentar `interval` ou `retries`** (por exemplo 10 s × 18, cerca de 3 min) | Só `app.json`. Absorve picos curtos | Só atrasa: saturação longa continua dando `unhealthy`. Atrasa também a detecção de banco fora no painel e o `service_healthy` na criação do `nginx` no deploy, se a sonda falhar no início. Não muda nenhuma consequência real, já que nada age sobre a saúde |
| **D. Sonda da `api` sem HTTP**, no molde de `worker` e `maintenance` (`app.json:110-122`): confere o processo em `/proc/1/cmdline` e faz `SELECT 1` direto no banco | Fica fora do teto sem tocar no código da app. Consistente com os outros serviços | Uma conexão de banco fora do pool a cada 10 s, justamente quando o banco está lento. Deixa de provar que o HTTP responde: um laço de eventos travado pareceria saudável. Não resolve a sonda do `nginx` |
| **E. Sonda do `nginx` só sobre o próprio Nginx** (`location = /nginx-health { allow 127.0.0.1; deny all; return 200; }`) | O status do `nginx` passa a dizer se ele serve a SPA, separado da API. Barata | Muda o template do Nginx e o `app.json`. O agregado do Coolify continua `degraded` pela `api`. Sozinha, não trata a `api` |

## Decisão (proposta)

**Recomendação: A**, com o teste da seção Compliance. **E** fica como complemento opcional se o dono quiser que o status do `nginx` reflita só a SPA. Se uma das premissas da tabela mudar (rótulos do Traefik no `nginx`, `autoheal`, Swarm, verificação de saúde no Caddy, ou o Coolify passar a reiniciar ou notificar por saúde), a escolha passa a ser **B** na `api` e **E** no `nginx`, num ADR que substitua este.

**Bottom line:** aceitar o `unhealthy` sob saturação, porque nesta topologia ninguém age sobre ele e o sinal é verdadeiro (a API está recusando). O custo é um status ambíguo no painel do Coolify, que confunde saturação com banco fora até alguém ler o log ou o alerta do #240. A alternativa que elimina o problema na raiz (B) custa um entrypoint próprio e testes de processo para defender contra um comportamento que hoje não existe.

## Consequências

- Sob saturação de uns 50 s, o painel do Coolify mostra a `api` e o `nginx` como `unhealthy`. Isso é esperado e deve constar em `deploy/coolify/README.md` junto com o alerta `gs-causa-api-saturada` (PR #240) como a forma de distinguir saturação de banco fora.
- O monitor de uptime avisa no Discord uma saturação que dure uns 4 min. É um aviso verdadeiro: os usuários estão recebendo 503.
- O deploy continua dependendo de a `api` nova ficar `healthy` antes do `nginx` ser criado. Como o Coolify para os containers antes do `up -d`, a `api` nova nasce sem carga, e o teto não interfere.
- A premissa "nada age sobre a saúde" vira regra conferida por teste. Quem quiser mudá-la precisa mudar o teste e, com ele, revisitar este ADR.
- A lacuna L2 da frente 5f muda de severidade: o modo de falha "sobrecarga parcial vira queda total" não existe na topologia atual.

## Compliance

Testes que provariam a escolha A (a escrever no PR que implementar a decisão):

1. **Processo, sem Docker** (`tests/test_api_probe_under_limit.py`): sobe o uvicorn real com as flags lidas do CMD do `docker/app.Dockerfile` e um app ASGI com uma rota lenta, ocupa `N - 1` vagas e afirma que o comando de sonda lido de `app.json` **sai com código diferente de zero** enquanto o teto está cheio e com **zero** depois que os pedidos terminam. É o comportamento aceito, escrito como teste: se alguém mudar a sonda ou o teto, o teste obriga a revisitar o ADR. Se o dono escolher B ou D, o mesmo teste passa a exigir **zero** com o teto cheio, e falharia com a configuração de hoje.
2. **Configuração** (`tests/test_probe_consumers.py`): falha se `api` ou `nginx` em `app.json` deixarem de ter `traefik.enable=false`; se algum serviço usar uma imagem ou um rótulo de `autoheal`; se os trechos de `docker/gateway/sites/*.caddy` ganharem `health_uri`, `health_port` ou `fail_duration`; ou se `restart` da `api` e do `nginx` deixar de ser uma política baseada em saída (`unless-stopped`, `always`, `on-failure`). Sabotagem esperada: tirar `traefik.enable=false` do `nginx` reprova o teste.
