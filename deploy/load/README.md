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
| Jornada | `scenarios/jornada.js` | 20 contas, cada uma enviando uma tabela, criando uma execução do csv-janitor, consultando `GET /runs/{id}` a cada 2 s e baixando o primeiro artefato. A rampa leva 20 s, sustenta 1m40s e desce em 10 s. | SLO de aceitação: `POST /runs` 202 com `p(99.5)<500` ms. SLO de execução: `gs_execucao_boa` `rate>=0.99`, em até 5 min. SLO de download: primeiro byte com `p(99.5)<1000` ms. Consulta: `p(99)<500` ms. Falhas abaixo de 0,5% e checks acima de 99,5%. |
| Fila | `scenarios/fila.js` | Com o worker parado, 12 contas pedem uma execução ao mesmo tempo. Cabem 10 na fila global; as outras recebem `503 QUEUE_FULL` com `Retry-After`. | No máximo 10 aceitas, pelo menos 1 fila cheia, 0 respostas inesperadas. |
| Transferências | `scenarios/transferencias.js` | Uma conta envia 3 arquivos de 24 MiB ao mesmo tempo, em 5 rodadas. Cabem 2 por conta; o terceiro recebe `429 RATE_LIMITED` com `Retry-After`. | Pelo menos 1 recusa, 0 respostas inesperadas, 0 falhas. |
| Limite por conta | `scenarios/limite-por-conta.js` | 120 leituras, 60 comandos e 10 `POST /runs` por minuto passam; a requisição seguinte recebe `429 RATE_LIMITED` com `Retry-After`. | Tudo dentro do limite passa e tudo acima é recusado. **Espera a A2** (PR #26): desligado por padrão, liga com `GS_LOAD_RATE_LIMIT=1`. |
| Limite de `/auth/*` | `scenarios/limite-auth.js` | O `limit_req` do Nginx deixa passar 11 seguidas (10/min com rajada de 10); a 12ª recebe 429. | Idem. **Espera a fatia 6** (PR #27): desligado por padrão, liga com `GS_LOAD_AUTH=1`. |

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
deploy/load/run-local.sh                # de 3 a 4 min; sai com 0 só se todos os cenários passarem
GS_LOAD_RATE_LIMIT=1 GS_LOAD_AUTH=1 deploy/load/run-local.sh   # depois da A2 e da fatia 6
```

O script:

1. confere o alvo (`GS_LOAD_TARGET`, padrão `http://nginx`) e se a API do projeto (`GS_LOAD_PROJECT`, padrão
   `goblinshift`) está rodando;
2. cria 20 contas de teste com `docker compose exec api gs-admin create-user` e emite uma chave para cada
   uma, válida por 1 dia. As chaves ficam num diretório temporário com modo 700, montado só para leitura no
   container do k6, e são **revogadas ao sair**, mesmo em caso de falha;
3. para o worker e a manutenção, roda a fila e os religa. A manutenção vive no namespace de PIDs do worker
   e morreria com ele;
4. roda as transferências e a jornada, e depois os cenários adiados, se ligados;
5. grava o resumo de cada cenário em `deploy/load/results/<cenário>.json` (ignorado pelo git, ou no
   diretório de `GS_LOAD_RESULTS`).

Para isolar da pilha de desenvolvimento, use outro projeto e outras portas:
`GS_HTTP_PORT=8088 GS_OPENOBSERVE_PORT=5088 docker compose -p goblinshift-carga up -d --wait` e
`GS_LOAD_PROJECT=goblinshift-carga deploy/load/run-local.sh`.

O k6 roda pela imagem `grafana/k6:2.3.0`, sem instalação local. As contas criadas ficam no banco local
(elas não têm como ser apagadas pelo `gs-admin`). Um `docker compose down -v` limpa tudo.

## Resultados

Rodada de 2026-10-14 na `main` (`6c1c41f`), num MacBook com Docker Desktop (10 CPUs, 8 GiB para a VM).
A pilha foi recriada do zero (`down -v`) e roda no projeto `goblinshift-carga`. Outras suítes do mesmo
Docker rodavam ao mesmo tempo, então os números são indicativos, não de referência.

| Cenário | Resultado | Números |
|---|---|---|
| Fila | passou | 12 pedidos: 10 aceitos e 2 `503 QUEUE_FULL` com `Retry-After`, 0 inesperados. `http_req_failed` 0/58. |
| Transferências | passou | 5 rodadas de 3 envios de 24 MiB: 5 recusas `429 RATE_LIMITED` com `Retry-After`, 0 inesperadas, 0/20 falhas. Duração p95 de 928 ms. |
| Jornada | passou | 1077 requisições (8,2 req/s), 0 falhas, 1037/1037 checks e 58/58 execuções boas. `POST /runs` 202: p95 de 135 ms e p99.5 de 369 ms (limite de 500). `GET /runs/{id}`: p95 de 94 ms e p99 de 173 ms (limite de 500). Download, primeiro byte: p95 de 103 ms e p99.5 de 150 ms (limite de 1000). |
| Limite por conta | reprovou, como esperado na `main` | Rodado uma vez à parte com `GS_LOAD_RATE_LIMIT=1`: as 3 requisições acima do limite passaram (`gs_acima_do_limite_recusado` 0%), porque o limite ainda não existe. Isso confirma que o threshold reprova sem a A2. |

Em uma rodada anterior, com o Docker bem mais disputado e antes da correção que religa a manutenção, a
jornada reprovou: p99 de 31,9 s no `GET /runs/{id}`. O worker registrou o lançador do Runner local
estourando os 10 s (`subprocess.TimeoutExpired` em `runner.py`), e as tentativas ficaram presas em
`running`. É o defeito tratado no PR #23 (`fix-runner-timeout`). Os thresholds pegaram a degradação, que
é o papel deles.
