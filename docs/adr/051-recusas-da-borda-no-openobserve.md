# ADR 051. Recusas da borda (429 do Nginx) no OpenObserve

- **Estado:** proposto (2026-11-10). Decisão do dono: a alternativa recomendada muda o template do Nginx, a configuração do Collector e `deploy/coolify/infra.json`, que são produção. Este ADR não altera código nem configuração.
- **Plano:** [3.7](../GoblinShiftPlan.md#s3-7) (sinais, SLOs e alertas) e [3.8](../GoblinShiftPlan.md#s3-8) (deploy e VPS)
- **Relacionado:** [ADR 041](041-ip-do-cliente-atras-da-cloudflare.md) (IP real do cliente e chave do limite), [ADR 048](048-limite-por-ip-na-borda-da-app-e-error-log-em-crit.md) (zonas `limit_req` e teto do uvicorn), [ADR 050](050-sondas-de-saude-sob-o-teto-de-concorrencia.md) (sondas sob o teto), PR #240 (alerta `gs-causa-api-saturada`), lacuna L4 da frente 5f

## Contexto

O lote de 10/11 pôs dois mecanismos de recusa na frente da API (ADR 048):

| Recusa | Onde nasce | Chega ao OpenObserve? |
|--------|------------|------------------------|
| **503 do teto do uvicorn** (`--limit-concurrency 30`, `docker/app.Dockerfile:52`) | No uvicorn, antes do ASGI: sem span nem `request_id` | **Sim, como causa.** Um handler no logger `uvicorn.error` conta as recusas, e a API exporta o span `api.saturation` por minuto. O alerta urgente `gs-causa-api-saturada` (`deploy/openobserve/sql/causa-api-saturada.sql`) dispara com recusas em 3 dos últimos 10 minutos. Já está na `main` (PR #240) |
| **429 do `limit_req`** (zonas `api` 20 r/s e rajada de 40, `readyz` 5 r/s, `auth` 10 r/min; `docker/nginx/templates/default.conf.template:40-51`) | No Nginx, pelas locations `@rate_limited` e `@auth_rate_limited`, sem chamar a API | **Não.** Só fica no log de acesso do Nginx, no driver `local` do Docker. O README do OpenObserve registra isso ("Fora do OpenObserve, por enquanto"), com um roteiro manual de `docker logs` |

O Collector (`docker/otel-collector/config.yaml`) só recebe OTLP nos logs, e a aplicação não exporta logs. `tests/openobserve/test_saturation_runbook.py::test_logs_dos_containers_nao_chegam_ao_openobserve` trava essa premissa: falha se algum receptor que não seja OTLP entrar num pipeline de logs.

**As recusas ficam fora dos SLOs, e isso é deliberado.** O plano tira do SLO de aceitação as recusas de política, porque "são o serviço se protegendo" (3.7). O 503 do teto também fica fora: o pedido recusado não gera evento `main`. A consequência é que o denominador encolhe. Com metade dos pedidos recusada na borda, a aceitação continua verde, porque mede só o que passou. Para o 503, o alerta de causa fecha a lacuna. Para o 429, não há nada.

**O cenário que importa** (frente 5f, L4). O `real_ip` do Nginx confia só no Caddy, em `172.31.0.2` (`default.conf.template:10-11`). Se uma mudança no Coolify ou no gateway trocar esse IP, o `X-Forwarded-For` deixa de ser aceito, e `$remote_addr` passa a ser o IP do proxy para todo mundo. A chave do limite (ADR 041) colapsa numa só, e com tráfego real a zona `api` recusa a maioria dos pedidos. Nenhum sinal acende:
- os SLOs ficam verdes;
- o `gs-causa-api-saturada` não dispara, porque o pedido nem chega ao uvicorn;
- o monitor de uptime externo (`deploy/uptime/README.md`) consulta `/readyz`, que tem zona própria e quase nenhum tráfego de usuário, e continua recebendo 200.

**O mesmo colapso também pode vir do gateway, e esse é o caso que já aconteceu.** O ADR 041 registra o Nginx logando `172.69.39.0`, um IP da borda da Cloudflare. Hoje, o matcher `@cloudflare` de `docker/gateway/sites/goblinshift.dev.caddy` só usa o `CF-Connecting-IP` quando a conexão vem de uma faixa de `docker/gateway/sites/cloudflare-ips.conf`. Se essa lista ficar desatualizada, o Caddy manda ao Nginx o IP de borda da Cloudflare. O `real_ip` aceita esse IP, e a chave colapsa nos poucos IPs de borda, que são **públicos**.

O outro caso é um limite apertado demais para o uso real, com muitos clientes legítimos recebendo 429 ao mesmo tempo. Também passa sem sinal.

Um cliente só, num laço ou abusando, também recebe 429. Isso **não** deve acordar ninguém: é o limite cumprindo o papel, e o alerta não seria acionável (3.7, "só o que é acionável").

No log de acesso (`access-log.conf.template`, formato `privacy`), a linha da recusa do `limit_req` já distingue os dois casos acionáveis, sem dado novo:
- `"status":429` com `"upstream_status":""` é a recusa da própria borda. O 429 da API (cota, transferências) vem com `"upstream_status":"429"`;
- `client` com o IP truncado em `/24` ou `/48`. No colapso pelo Nginx, ele é o da rede `edge`, `172.31.0.0`, um endereço privado que nunca é cliente real. No colapso pelo gateway, ele cai numa faixa da Cloudflare, que também nunca é cliente real depois do gateway.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Manter como está:** o 429 só no log dos containers, com roteiro manual | Nenhuma mudança. Hoje, com o cadastro fechado, quase não há tráfego para recusar | Cego para o colapso da chave e para o limite apertado. Alguém só olha o log depois de um usuário reclamar. Com o cadastro aberto (fatia 8), o ponto cego passa a ter usuários reais atrás |
| **B. Monitor externo também numa rota de `/api/`.** O Worker de uptime passa a pedir, por exemplo, `GET /api/v1/runs` sem credencial, espera o 401 da API e avisa com dois 429 seguidos | Sem compose e sem telemetria nova: muda só `deploy/uptime/`. O pedido do Worker cai na mesma chave colapsada e vê o 429 que os usuários veem. O 401 sem credencial é barato. Falta conferir se ele chega a consultar o banco: a sessão entra como dependência de `authenticate` (`src/goblinshift/entrypoints/api/dependencies.py:135`) | Pega só o colapso, e com chance proporcional à fração recusada: com 1/3 de recusa, dois 429 seguidos têm cerca de 11% de chance a cada 4 min. É um sinal instável. Não pega o limite apertado nem o abuso distribuído. Mais um pedido a cada 2 min, contando na zona `api` |
| **C. Nginx envia só as recusas da borda ao Collector por syslog UDP; o Collector as leva ao stream de logs; dois alertas de causa.** Uma segunda diretiva `access_log syslog:server=<IP do Collector>:<porta> privacy if=$edge_refused` (um `map` que vale 1 só com status 429 e `$upstream_status` vazio), um receptor `syslog` ou `udplog` com parser JSON no Collector contrib, num pipeline `logs/edge`, e o IP fixo do Collector na rede `internal` | Sinal completo e preciso, que separa os três casos (colapso, limite apertado, abuso de um cliente). Usa o formato `privacy`, já provado sem IP completo nem query. Volume pequeno, só as recusas. Por UDP, o Nginx não espera nem falha se o Collector estiver fora: a linha se perde, e o log local continua. A imagem contrib 0.162.0 já está em `infra.json`, e os dois serviços já dividem a rede `internal` | Exige compose (decisão do dono): IP fixo para o Collector em `infra.json`, com duas opções. **C1. Na `goblinshift_internal`.** A rede é `external: true` em `infra.json` e em `app.json`, e o repositório não a cria nem declara sub-rede. Se ela foi criada na VPS sem `--subnet` (conferir antes com `docker network inspect goblinshift_internal`), não dá para acrescentar a sub-rede depois: é preciso recriar a rede, desconectando os containers das duas pilhas, o que dá queda e pede roteiro próprio. **C2. Na `edge`.** A rede já tem sub-rede e faixa estática (`deploy/edge/compose.yaml:33`; o Nginx usa `172.31.0.4`), mas a porta UDP fica exposta a todos os containers da `edge`, que a VPS divide com outro projeto (ADR 014). O Nginx resolve o nome do servidor de syslog **ao carregar a configuração** (documentação do Nginx; não conferido nesta versão). Com o nome `otel-collector`, o Nginx deixaria de subir sem a pilha de infra, e a telemetria derrubaria o site. Sob ataque, cada recusa vira um pacote e uma linha: sem agregação no Collector (por exemplo o processador `logdedup` do contrib, a confirmar na 0.162.0), uma rajada pode encher o limite de ingestão do OpenObserve, que já respondeu 503 por 20 min (README, "Limite de ingestão"). Guarda o cliente truncado no OpenObserve por 30 dias, dentro dos 90 dias de 3.6 para eventos de segurança. Inverte a premissa travada por `test_logs_dos_containers_nao_chegam_ao_openobserve` |
| **D. Nginx escreve as recusas num arquivo de um volume compartilhado; o Collector lê com `filelog`** | Também não depende de rede nem de DNS | Volume entre dois recursos do Coolify (`app.json` e `infra.json`). O Nginx não gira o próprio arquivo, então seria preciso `logrotate` ou o sinal de reabrir, e disco ocupado no caminho do Nginx. É mais peça que C pelo mesmo sinal |
| **E. Collector lê os logs do Docker** (`filelog` em `/var/lib/docker/containers`) | Pega todo o log de acesso sem mudar o Nginx | O driver `local` grava em formato binário: exigiria trocar para `json-file`. Montar o diretório do Docker expõe ao Collector o log de **todos** os containers da VPS, que é compartilhada com outro projeto (3.8, ADR 014). Não recomendada |
| **F. Contador na aplicação**, como o `api.saturation` | Já é a solução adotada para o 503 (PR #240), sem ingestão de logs | Não serve para o 429: a recusa do `limit_req` nunca chega à API. Encaminhar as recusas para a API, por `mirror` ou subpedido, devolveria a ela a carga que o limite existe para cortar, e cada recusa ocuparia o teto de 30 |
| **G. Métrica do Nginx ou do uvicorn** | Métrica é barata de armazenar e de consultar | O uvicorn não expõe métricas, e o 503 já é contado pelo log (F). O `stub_status` do Nginx OSS não conta por status. Por status, só com o módulo VTS ou o NGINX Plus, ou seja, imagem própria do Nginx |
| **H. Contar as recusas da borda como eventos ruins nos SLOs** | O SLO deixaria de ficar verde com usuários recusados | Contraria 3.7: recusa de política é o serviço se protegendo e não gasta orçamento. Um cliente abusivo esgotaria o orçamento de todos e congelaria as fatias pela política de orçamento. O sinal certo é de causa, não de sintoma |

## Decisão (proposta)

**Recomendação: C, antes de abrir o cadastro (fatia 8). Até lá, A.** O 503 do teto fica como está: o alerta de causa do PR #240 já o cobre, e as recusas continuam fora dos SLOs.

A forma proposta de C:

1. **Nginx:** um `map` define `$edge_refused` como 1 só com `$status = 429` e `$upstream_status` vazio, o que cobre as zonas `api`, `readyz` e `auth`. Uma segunda diretiva `access_log syslog:server=<IP fixo do Collector>:5514,tag=gs_edge privacy if=$edge_refused;` fica ao lado do `access_log` local, que não muda. O servidor vai por IP, nunca por nome, para o Nginx subir sem a pilha de infra.
2. **Collector:** um receptor de syslog em UDP, na rede escolhida no item 4, com o parser JSON das linhas `privacy`. Um processador agrega as recusas por (`client`, `path`, `status`) a cada 10 s, com a contagem num atributo, para uma rajada não virar uma linha por pedido. O stream de logs próprio exige uma segunda instância do exportador `otlphttp`, com outro header `stream-name`. A instância atual manda tudo para `default` (`docker/otel-collector/config.yaml:31-35`).
3. **Dois alertas de causa** no catálogo (`deploy/openobserve/catalog.py`, com `stream_type="logs"`):
   - **`gs-causa-borda-sem-ip-real`** (urgente): qualquer recusa da borda nos últimos 10 min cujo `client` caia em uma de duas faixas.
     - **Endereço privado** (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`): o `real_ip` do Nginx deixou de confiar no proxy. A ação é conferir o IP do Caddy na rede `edge` e o `set_real_ip_from`.
     - **Faixa da Cloudflare** (`docker/gateway/sites/cloudflare-ips.conf`): o gateway deixou de trocar o IP de borda pelo `CF-Connecting-IP`. A ação está no ADR 041: atualizar a lista e conferir o matcher `@cloudflare`.

     A lista de faixas do alerta é gerada do mesmo `cloudflare-ips.conf`, nunca copiada à mão, e o teste de configuração amarra as duas. **Limitação:** depois de truncar em `/24`, um IP de borda só é reconhecido se a faixa da Cloudflare cobrir esse `/24`. As faixas atuais cobrem: em IPv4, de `/13` a `/22`, e em IPv6, de `/29` a `/32`, contra o `/48` do truncamento. Uma faixa futura mais estreita que o truncamento ficaria de fora. Se a lista do Caddy e a do alerta envelhecerem juntas, o critério cego é o mesmo do matcher: a faixa nova da Cloudflare não entra em nenhuma das duas. O sinal só se recupera com a atualização da lista, e o aviso abaixo pode disparar como "limite apertado" e levar à ação errada. Por isso, o texto desse aviso manda conferir primeiro se os `client` são IPs da Cloudflare;
   - **`gs-causa-borda-limitando`** (aviso): recusas de 5 ou mais clientes distintos em pelo menos 3 dos últimos 10 minutos. O limite está apertado para o uso real. A ação é revisar as zonas por PR, com `tests/test_nginx_limit_consumers.py`. Os números são uma calibragem inicial, a revisar com o tráfego da fatia 8. A contagem usa o `client` truncado em `/24` ou `/48`, mas a chave do limite é o IP ou o `/64` (`default.conf.template:33-37`). Com isso, CGNAT e redes compartilhadas contam como um cliente só, e o aviso subconta esses casos.

   Um cliente só, por mais recusas que tenha, não dispara nada: é o limite funcionando. O roteiro manual do README continua para investigar.
4. **`infra.json`:** IP fixo do Collector, em C1 ou C2 (ver Alternativas). É decisão do dono.
   - **C1** (`goblinshift_internal`): primeiro conferir a IPAM com `docker network inspect goblinshift_internal`. Sem sub-rede, recriar a rede com queda e roteiro próprio.
   - **C2** (`edge`): sem queda, mas a porta UDP fica visível ao outro projeto. Isso aumenta o risco de linhas forjadas (ver Consequências).

   A recomendação é C1 se a IPAM já tiver sub-rede, e C2 se não tiver.

**Não recomendadas:**
- B, como solução: dá um sinal instável e cobre só o colapso. Serve de paliativo se C demorar além da fatia 8;
- D, que tem mais peças que C pelo mesmo sinal;
- E, que expõe os logs do outro projeto;
- F e G, que não enxergam o 429;
- H, que contraria a política de orçamento de 3.7.

**Bottom line:** C, porque é a única alternativa que vê o 429 da borda. Ela separa três casos:
- o colapso da chave, no Nginx ou no gateway, enquanto a lista da Cloudflare estiver em dia (urgente);
- o limite apertado (aviso);
- o abuso de um cliente (nada).

Além disso, reaproveita o formato de log já provado sem dado pessoal. O custo é:
- uma mudança coordenada em três lugares de produção (template do Nginx, Collector e `infra.json`);
- o IP fixo, que pode exigir recriar a `goblinshift_internal` com queda, ou expor a porta na `edge`;
- um stream de logs novo no OpenObserve;
- a obrigação de agregar no Collector, para que um ataque não esgote a ingestão. Enquanto o cadastro estiver fechado, A basta, porque não há usuário real para recusar.

## Consequências

- **Sinal novo:** o colapso da chave passa a acender um alerta urgente em até cerca de 10 min (agregação de 10 s, exportação e avaliação a cada 5 min). Hoje ele não acende nada. O limite apertado vira aviso.
- **SLOs sem mudança:** as recusas da borda e do teto continuam fora do orçamento, como em 3.7. O texto do README ("Fora do OpenObserve, por enquanto") e a lista de causas de 3.7 ganham as duas causas novas.
- **Primeiro stream de logs no OpenObserve.** Hoje só há traces e métricas, e o stream novo entra na retenção de 30 dias. O provisionamento precisa aceitar `stream_type="logs"` nos alertas, o que muda a afirmação de `test_logs_dos_containers_nao_chegam_ao_openobserve`.
- **Dado pessoal:** o OpenObserve passa a guardar o cliente truncado (`/24` ou `/48`) e o caminho, sem query, das recusas, por 30 dias. Isso cabe nos 90 dias de 3.6 para eventos de segurança e fica na VPS (3.7).
- **Acoplamento:** o Nginx passa a conhecer o IP do Collector. Se o Collector mudar de IP sem o template acompanhar, as linhas se perdem em silêncio, e o alerta fica cego sem disparar. O teste de configuração abaixo amarra os dois valores.
- **Ataque:** sem a agregação, uma rajada de recusas vira carga de ingestão e pode cegar a telemetria toda por até 20 min. Com ela, o volume fica limitado pelo número de pares (`client`, `path`) distintos por janela. A agregação não protege o próprio Collector. O `memory_limiter` tem `limit_mib: 256` (`config.yaml:26-28`), igual ao limite de memória do container em `infra.json`, então sob uma rajada UDP o OOM chega antes do limitador. O problema já existia, mas C passa a pôr no Collector um volume controlado por quem ataca: o `limit_mib` precisa ficar abaixo do limite do container, com folga.
- **Collector fora:** pela documentação do Nginx, com o destino UDP sem ninguém ouvindo, o envio pode gravar uma linha de erro por recusa no log de erro do processo. Não foi conferido. A linha não tem dado de cliente, mas faz ruído sob ataque.
- **Linhas forjadas (STRIDE, spoofing):** qualquer container que alcance a porta UDP pode mandar linhas falsas e acender o alerta urgente sem motivo. O alcance é a `internal` em C1 e também o outro projeto em C2. O risco é baixo, porque o efeito é um alerta, não dado nem acesso. A mitigação é o receptor ouvir só na interface da rede escolhida. O teste é uma linha enviada de fora dessa rede, que não chega ao stream.
- **Rede do IP fixo (decisão do dono):**
  - C1 pode exigir recriar a `goblinshift_internal`, com queda das duas pilhas durante a troca;
  - C2 não tem queda, mas expõe a porta ao outro projeto da VPS.
- **Fora do escopo:** o 502 e o 504 do Nginx com a API fora também não chegam ao OpenObserve. Esse caso já é coberto pelo monitor externo do `/readyz`. O mesmo `map` poderia incluí-los depois, mas só com uma ação clara que o monitor externo ainda não dê.

## Compliance

Testes que provam a escolha C (a escrever no PR que a implementar):

1. **Nginx real** (`tests/test_nginx_logs.py` ou `tests/test_nginx_edge_limits.py`): um ouvinte UDP no lugar do Collector recebe **exatamente uma** linha `privacy` por recusa do `limit_req` nas três zonas, e nenhuma linha para 200, para o 429 vindo da API (`upstream_status` `429`) nem para o 413. Os canários de IP completo e de query não aparecem na linha recebida. Sabotagem: tirar o `if=$edge_refused` faz o 200 aparecer e reprova o teste.
2. **Nginx sobe sem o Collector:** com o IP de syslog sem ninguém ouvindo, o container sobe e serve `/readyz`. Isso falha se alguém trocar o IP pelo nome `otel-collector`. O mesmo teste conta as linhas do log de erro durante uma rajada de recusas e afirma que nenhum canário de IP ou de query aparece nelas.
3. **Configuração** (`tests/openobserve/test_collector_config.py`) confere:
   - o pipeline `logs/edge` tem o receptor de syslog, o processador de agregação e o exportador próprio;
   - o endereço de syslog do template do Nginx é igual ao IP fixo do Collector em `infra.json`;
   - o `memory_limiter` fica abaixo do limite de memória do container;
   - as faixas da Cloudflare do alerta são as de `docker/gateway/sites/cloudflare-ips.conf`, e nenhuma é mais estreita que `/24`. A premissa de `test_saturation_runbook.py` muda de "nenhum log de container" para "só as recusas da borda, só por este pipeline".
4. **Alertas no OpenObserve real** (`tests/openobserve/test_alert_firing.py`, com Docker), semeando linhas:
   - recusas com `client` `172.31.0.0` disparam `gs-causa-borda-sem-ip-real`;
   - recusas com `client` `172.69.39.0` (faixa da Cloudflare, o caso do ADR 041) disparam o mesmo alerta;
   - 5 clientes públicos distintos em 3 minutos disparam `gs-causa-borda-limitando`;
   - um cliente público só, com centenas de recusas, não dispara nenhum dos dois.

   Sabotagem: trocar o filtro de endereço privado por qualquer cliente faz o terceiro caso disparar e reprova o teste.

Enquanto valer A (até a fatia 8), o que garante a premissa é o teste atual, `test_logs_dos_containers_nao_chegam_ao_openobserve`, junto com o roteiro manual do README. Se o dono escolher B como paliativo, o teste fica em `deploy/uptime/uptime.test.mjs`: 401 é saudável, dois 429 seguidos avisam e um 429 isolado não avisa.
