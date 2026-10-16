# Teste de carga (k6)

O objetivo do plano (fatia 8, 2.5) é **confirmar os limites** por classe de rota e os SLOs sob uma carga
realista. Não se mede a capacidade máxima. Cada cenário tem `thresholds` que reprovam o processo do k6,
então o resultado é passa ou não passa.

**Só roda contra o Compose local.** O `run-local.sh` recusa, com saída 2, qualquer alvo que não seja o
Nginx do Compose (`http://nginx`), a própria máquina (`localhost`, `127.x`, `host.docker.internal`) ou
um IP privado (10/8, 172.16/12, 192.168/16). URL com credencial embutida também é recusada. Rodar contra
a VPS fica fora deste script: lá a máquina é compartilhada com o StillOn (ADR 011), e essa janela pede
uma decisão própria.

## Cenários

| Cenário | Arquivo | O que confirma | Thresholds |
|---|---|---|---|
| Jornada | `scenarios/jornada.js` | 20 contas, cada uma enviando uma tabela, criando uma execução do csv-janitor, consultando `GET /runs/{id}` a cada 2 s e baixando o primeiro artefato. A rampa leva 20 s, sustenta 1m40s e desce em 10 s. `503 QUEUE_FULL` e `429 RATE_LIMITED` (as novas tentativas contam no limite de 10 `POST /runs` por minuto da conta) são recusas previstas: o VU confere o `Retry-After` e espera 5 s. | SLO de aceitação: `POST /runs` 202 com `p(99.5)<500` ms. SLO de execução: `gs_execucao_boa` `rate>=0.99`, em até 5 min. SLO de download: primeiro byte com `p(99.5)<1000` ms. Consulta: `p(99)<500` ms. Falhas abaixo de 0,5% e checks acima de 99,5%. |
| Fila | `scenarios/fila.js` | Com o worker parado, 12 contas pedem uma execução ao mesmo tempo. Cabem 10 na fila global; as outras recebem `503 QUEUE_FULL` com `Retry-After`. | No máximo 10 aceitas, pelo menos 1 fila cheia, 0 respostas inesperadas. |
| Transferências | `scenarios/transferencias.js` | Uma conta envia 3 arquivos de 24 MiB ao mesmo tempo, em 5 rodadas. Cabem 2 por conta; o terceiro recebe `429 RATE_LIMITED` com `Retry-After`. | Pelo menos 1 recusa, 0 respostas inesperadas, 0 falhas. |
| Limite por conta | `scenarios/limite-por-conta.js` | 120 leituras, 60 comandos e 10 `POST /runs` por minuto passam; a requisição seguinte recebe `429 RATE_LIMITED` com `Retry-After`. | Tudo dentro do limite passa e tudo acima é recusado. Liga por padrão; `GS_LOAD_RATE_LIMIT=0` desliga. |
| Limite de `/auth/*` | `scenarios/limite-auth.js` | O `limit_req` do Nginx deixa passar 11 seguidas (10/min com rajada de 10); a 12ª recebe 429. | Idem. Liga por padrão; `GS_LOAD_AUTH=0` desliga. |

Os números dos SLOs vêm de `deploy/openobserve` (`catalog.py` e `sql/*.eventos.sql`).
`tests/test_load_script.py` lê esses arquivos e reprova se um threshold deixar de acompanhar o SLO.
No SLO de execução, o k6 mede do 202 até a consulta que vê o estado terminal. Esse tempo inclui a fila
e o intervalo de 2 s, então o critério fica mais severo que o SLO, que conta a partir da primeira
tentativa.

O cenário de 3 uploads simultâneos depende de sobreposição. Na rede local, um envio de 24 MiB leva
centenas de milissegundos, e as 5 rodadas bastaram para as recusas aparecerem em todas as execuções
até agora. Se um dia não aparecer nenhuma, o threshold `count>=1` reprova e o cenário precisa de
arquivos maiores ou de mais rodadas.

## Como rodar

```bash
./scripts/dev-secrets.sh
docker compose up -d --build --wait     # a pilha local (compose.yaml + compose.override.yaml)
deploy/load/run-local.sh                # cerca de 4 min; sai com 0 só se todos os cenários passarem
GS_LOAD_RATE_LIMIT=0 GS_LOAD_AUTH=0 deploy/load/run-local.sh   # sem os cenários de limite
```

O script:

1. confere o alvo (`GS_LOAD_TARGET`, padrão `http://nginx`) e se a API do projeto (`GS_LOAD_PROJECT`, padrão
   `goblinshift`) está rodando;
2. cria 20 contas de teste com `docker compose exec api gs-admin create-user` e emite uma chave para cada
   uma, válida por 1 dia. As chaves ficam num diretório temporário com modo 700, montado só para leitura no
   container do k6, e são **revogadas ao sair**, mesmo em caso de falha;
3. para o worker e a manutenção, roda a fila e os religa. A manutenção vive no namespace de PIDs do worker
   e morreria com ele;
4. roda as transferências, a jornada e os cenários de limite (por conta e de `/auth/*`), salvo os
   desligados com `0`;
5. grava o resumo de cada cenário em `deploy/load/results/<cenário>.json` (ignorado pelo git, ou no
   diretório de `GS_LOAD_RESULTS`).

Para isolar da pilha de desenvolvimento, use outro projeto e outras portas:
`GS_HTTP_PORT=8088 GS_OPENOBSERVE_PORT=5088 docker compose -p goblinshift-carga up -d --wait` e
`GS_LOAD_PROJECT=goblinshift-carga deploy/load/run-local.sh`.

O k6 roda pela imagem `grafana/k6:2.3.0`, sem instalação local. As contas criadas ficam no banco local
(elas não têm como ser apagadas pelo `gs-admin`). Um `docker compose down -v` limpa tudo.

## Resultados

Rodada de 2026-10-16 na `main` (`a6025be`) com os cenários de limite ligados por padrão, num MacBook com
Docker Desktop (10 CPUs, 8 GiB para a VM). A pilha foi criada do zero no projeto `goblinshift-carga`, e
outro projeto usava a máquina ao mesmo tempo, então os números são indicativos, não de referência. A
rodada completa levou 3min35s.

| Cenário | Resultado | Números |
|---|---|---|
| Fila | passou | 12 pedidos: 10 aceitos e 2 `503 QUEUE_FULL` com `Retry-After`, 0 inesperados. `http_req_failed` 0/58. |
| Transferências | passou | 5 rodadas de 3 envios de 24 MiB: 5 recusas `429 RATE_LIMITED` com `Retry-After`, 0 inesperadas, 0/20 falhas. Duração p95 de 318 ms. |
| Jornada | passou | 1405 requisições (10,7 req/s), 0 falhas, 1365/1365 checks e 160/160 execuções boas. 2 `POST /runs` receberam `429 RATE_LIMITED` com `Retry-After`. `POST /runs` 202: p95 de 43 ms. `GET /runs/{id}`: p95 de 25 ms. |
| Limite por conta | passou | 190/190 dentro do limite (120 leituras, 60 comandos e 10 `POST /runs`) e 3/3 acima dele recusadas com `429 RATE_LIMITED` e `Retry-After`. `http_req_failed` 0/193. |
| Limite de `/auth/*` | passou | 11/11 dentro da rajada chegaram ao login (303) e a 12ª recebeu `429 RATE_LIMITED` do Nginx. `http_req_failed` 0/12. |

Na primeira rodada desse dia, antes de ajustar dois cenários, tudo passou, mas com dois sinais. A jornada
teve 6 checks falhos (0,41%, abaixo do teto de 0,5%): eram `429` da API em `POST /runs`, o limite da A2
agindo como especificado sobre as novas tentativas após a fila cheia, que o cenário ainda não tratava. E o
`limite-auth` batia em `/auth/github/login`, que não existe (404). Ele passou a usar `/auth/login/github`
e reprova com `http_req_failed` acima de 0, para pegar uma rota que deixe de existir.

Na rodada de 2026-10-14 (`6c1c41f`), sem a A2, o cenário do limite por conta reprovou como esperado (as 3
requisições acima do limite passaram), o que confirmou que o threshold reprova sem o limite. Numa rodada
anterior, com o Docker bem mais disputado e antes da correção que religa a manutenção, a jornada reprovou:
p99 de 31,9 s no `GET /runs/{id}`. O worker registrou o lançador do Runner local estourando os 10 s
(`subprocess.TimeoutExpired` em `runner.py`), e as tentativas ficaram presas em `running`. É o defeito
tratado no PR #23 (`fix-runner-timeout`). Os thresholds pegaram a degradação, que é o papel deles.
