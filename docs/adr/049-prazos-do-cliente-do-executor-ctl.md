# ADR 049. Prazos do cliente do `executor-ctl` derivados do teto do serviço

- **Estado:**
  - Prazos do cliente (190 s no `start`, 160 s no `remove`): **aceito** (PR #230, no lote de 10/11/2026).
  - Lock da área com prazo e `503 busy`, e `runsc` obrigatório em `load_config`: **proposto, entra com a reinstalação** do `executor-ctl` no host, depois de 11/11/2026 14:00 UTC, na mesma janela do #220 (PR #235).
- **Plano:** [3.2](../GoblinShiftPlan.md#s3-2) e [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 004](004-container-gvisor-por-tentativa.md) (um container gVisor por tentativa, criado pelo `executor-ctl`), [ADR 005](005-area-do-executor-em-filesystem-fixo.md) (área do executor), [ADR 047](047-pausa-de-claims-apos-falha-de-banco.md) (pausa dos claims)

## Contexto

O worker falava com o `executor-ctl` com 30 s de prazo no `start` (`START_TIMEOUT`) e 10 s no `remove`. O serviço, por sua vez, dá 30 s a cada chamada ao CLI do Docker (`deploy/executor_ctl/docker_cli.py:17`). O `start` faz três chamadas (`inspect`, `run`, `inspect`) e o `remove` duas (`inspect`, `rm`), e os dois tomam o lock da área, um `threading.Lock()` sem prazo (`deploy/executor_ctl/service.py:39,47,90`), num servidor `ThreadingMixIn`.

Quando o cliente desiste antes do serviço, a operação continua lá. No `start`, o container nasce sem ninguém esperando. O coordenador trata o timeout como falha transitória e chama `remove_work`, que não remove um executor em execução. A área fica ocupada, e o próximo claim recebe `work_area_dirty`, o mesmo efeito tratado pelo ADR 047. No `remove`, a thread continua na fila do lock, e cada repetição da manutenção empilha outra atrás dela.

O cliente vive na imagem do worker e não pode importar `deploy/executor_ctl`, que não vai na imagem nem está no `root_package` do import-linter.

## Alternativas

**Prazos do cliente:**

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Cliente espera mais que o pior caso do serviço, com o pior caso espelhado em constantes e conferido por teste de contrato** (escolhida) | O timeout deixa de ser ambíguo: se o cliente desistiu, o serviço já desistiu. Muda só o worker, sem reinstalar nada no host | Prazos longos (até 190 s), acima do lease de 60 s, que não é renovado durante o `start`. O espelho pode divergir do serviço, e só o teste o segura |
| **B. Prazo do cliente pela soma das chamadas do próprio `start` (90 s + margem)** | Mais curto | Ignora a espera do lock atrás de outra operação. O cliente ainda desistiria com a thread na fila |
| **C. Encurtar os prazos do serviço** | Cliente e serviço ficam rápidos | Exige reinstalar o `executor-ctl`. Um `docker run` lento, mas legítimo, viraria falha |

**Lock e runtime no serviço (#235):**

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. `acquire(timeout=5 s)` no lock e `503 busy`; `load_config` aceita só `runsc`** (escolhida) | A fila do lock deixa de crescer sem limite, e `busy` já é tratado como falha transitória no worker. O arquivo do host nunca aceita `runc` | Exige reinstalar o serviço. Uma operação legítima acima de 5 s faz a outra receber `busy` e ir para `retry_wait` |
| **B. Só os prazos do cliente, sem mudar o serviço** | Nada a reinstalar | Threads presas de versões antigas do worker e repetições da manutenção continuam empilhando no lock |
| **C. `runc` aceito com `require_mount: false`** (opção A do ruling) | Desenvolvimento local com `runc` a partir de arquivo | Acopla dois significados à mesma flag e torna a regra mais difícil de auditar |

## Decisão

**Prazos do cliente (alternativa A), aceito.** Em `src/goblinshift/runs/worker/executor_ctl_runner.py:41-64`:

- `CTL_DOCKER_CALL_TIMEOUT` = 30 s, espelho de `COMMAND_TIMEOUT_S`;
- `CTL_START_DOCKER_CALLS` = 3, `CTL_REMOVE_DOCKER_CALLS` = 2;
- `CTL_LOCK_HOLDER_DOCKER_CALLS` = 3: a pior operação inteira que pode estar à frente no lock, outro `start`;
- `START_MARGIN` e `REMOVE_MARGIN` = 10 s, para o trabalho fora do Docker (allowlist, posse da área, `rmtree`, HTTP);
- `START_TIMEOUT` = 30 × (3 + 3) + 10 = **190 s**; `REMOVE_TIMEOUT` = 30 × (2 + 3) + 10 = **160 s**.

O `CALL_TIMEOUT` de 10 s continua para `status` e `stop`, que não tomam o lock. O plano registra os dois prazos e a regra cliente > teto do servidor (`docs/GoblinShiftPlan.md:731`).

**Lock com prazo e `runsc` obrigatório (alternativa A), proposto.** Decidido no ruling da triagem (`frente-4-triagem.md:48`), implementado no PR #235, que só pode ser mesclado junto da reinstalação:

- `start` e `remove` tomam o lock por `acquire(timeout=5 s)`; estourado, o serviço responde `503` com `{"error": "busy"}`. Os 5 s ficam acima da duração normal de quem segura o lock (1 a 3 s).
- `load_config` aceita só `runtime: "runsc"`. `runc` só existe em `Config` montada nos testes.

## Consequências

- O timeout do `start` e do `remove` no worker deixa de deixar container ou thread para trás no caso normal (uma operação à frente no lock, que vale com um worker e uma manutenção).
- **O lease não é renovado durante o `start`.** Com 190 s de prazo contra 60 s de lease, um `start` lento pode passar do lease. O caminho se autocorrige: a manutenção encerra a tentativa pelo lease vencido, e quando o `start` volta, `record_start` perde o fencing e o worker abandona com `stop` e `remove`. O teste de integração `test_lease_vencido_durante_o_start_fica_com_a_manutencao_e_o_worker_abandona` cobre isso. A correção de verdade é renovar o lease durante o `start`, que fica para depois.
- **`status` e `stop` continuam com timeout ambíguo** (10 s no cliente contra 30 s e 60 s mais a carência no serviço). Não tomam o lock, então não acumulam fila; a ambiguidade é só de leitura.
- O `docker run` morto pelo timeout do `subprocess` não cancela a criação no daemon: o serviço responde 503 e o container pode nascer mesmo assim. Corrigir exige mudar o `executor-ctl`.
- O teto supõe o `observer` desligado, como em produção. Com um `ReceiptSink` de operador, que é síncrono e sem prazo, ele não vale.
- **Depois da reinstalação,** o termo `CTL_LOCK_HOLDER_DOCKER_CALLS` fica conservador: o pior caso real vira 5 s de espera do lock mais as chamadas próprias. Reduzir `START_TIMEOUT` e `REMOVE_TIMEOUT` é uma mudança pequena, a fazer depois da janela, com este ADR atualizado.
- **Antes da janela,** conferir que `/etc/goblinshift/executor-ctl.json` da VPS tem `"runtime": "runsc"`. Com `runc`, o instalador para no preflight, e um serviço reiniciado por fora do instalador não sobe (`ConfigError`).
- Sem a opção C do lock, perde-se rodar um `executor-ctl` local com `runc` a partir de arquivo, o que só afeta desenvolvimento.

## Compliance

- `tests/executor_ctl/test_client_deadlines.py` roda o `ExecutorService` real com um Docker que só conta as chamadas: o espelho do prazo é igual a `COMMAND_TIMEOUT_S`; o `start` faz `["inspect", "run", "inspect"]` e o `remove` `["inspect", "rm"]`; quem segura o lock cabe no espelho; o teto medido no serviço (180 s no `start`, 150 s no `remove`) é menor que o prazo do cliente.
- `tests/runs/test_executor_ctl_runner.py`: o runner padrão usa 190 s no `start`, 160 s no `remove` e 10 s no `status`; um `executor-ctl` lento, com prazos escalados, é aceito no ponto médio entre o prazo antigo e o novo.
- Sabotagens registradas no relatório da frente 4e (`START_MARGIN = 0`, `REMOVE_MARGIN = 0`, `CTL_LOCK_HOLDER_DOCKER_CALLS = 2`, `remove()` com `CALL_TIMEOUT`), todas reprovadas.
- Com o #235: `tests/executor_ctl/test_service.py` (lock liberado depois de exceção dentro do bloco), `tests/executor_ctl/test_area_busy.py` (`503 busy` no `start` e no `remove`, recebido pelo worker como falha transitória), `tests/executor_ctl/test_config.py` (configuração com `runc` recusada) e `test_area_ocupada_no_start_e_falha_transitoria_sem_tentativa_fisica` no coordenador. A aceitação na VPS (`sudo python3 -B -m acceptance.run`) roda na janela de reinstalação.
