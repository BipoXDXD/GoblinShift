# ADR 047. Pausa dos claims depois de falha de banco que deixa tentativa órfã

- **Estado:** aceito (PR #224, no lote de 10/11/2026)
- **Plano:** [3.4](../GoblinShiftPlan.md#s3-4)
- **Relacionado:** [ADR 002](002-postgresql-fila.md) (fila no PostgreSQL, com lease), [ADR 004](004-container-gvisor-por-tentativa.md) (um container por tentativa, criado pelo `executor-ctl`)

## Contexto

O #217 fez a supervisão absorver a falha transitória de banco na renovação do lease. Três passos fora dela ainda deixam a exceção subir até a volta do worker: `record_start`, a renovação do lease no empacotamento (`_LeaseKeeper`) e o desfecho (`finish`). Quando isso acontece, a Attempt fica viva, com container e área de trabalho, até a manutenção encerrá-la pelo lease vencido.

Nesse intervalo, o mesmo worker reivindica a Run seguinte, que pode ser de outra conta. O `executor-ctl` recusa o `start` com `work_area_dirty`, porque a área ainda é da tentativa órfã. A Run seguinte gasta um claim por um defeito que não é dela e vai para `retry_wait`. Repetido, isso a leva a `INFRA_POISON`.

O teste de integração reproduziu os três pontos antes da correção: a falha sobe até `_work_once`, a Attempt de R1 fica com `ended_at` nulo, e R2 termina em `running/retry_wait`, com `claim_count = 1` e `EXECUTOR_UNAVAILABLE` (PR #224).

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Pausar os claims do worker por uma janela depois da falha de banco** (escolhida) | Mudança local no laço do worker, sem tocar no `executor-ctl` nem no protocolo. A janela sai dos prazos que já existem na configuração. O caso normal não muda | A pausa vive na memória do processo e some num reinício. Atrasa a fila desse worker durante a janela, mesmo quando a limpeza termina antes |
| **B. Não reivindicar enquanto a área estiver suja** | Reage ao estado real, não a uma estimativa de tempo | Exige perguntar ao `executor-ctl` antes de cada claim, ou ler a área do host. Isso cria chamada nova no caminho quente e acopla o claim ao protocolo do serviço |
| **C. Não contar o claim quando o `start` voltar `work_area_dirty`** | A Run seguinte não chega a `INFRA_POISON` | Mascara o sintoma: o worker continua reivindicando e falhando em laço até a limpeza, e uma área suja por outro motivo deixaria de contar como defeito de infraestrutura |

## Decisão

Alternativa A, decidida pelo dono via coordenadora.

- **Gatilho:** só falha transitória de banco (`TRANSIENT_DATABASE_ERRORS`: `OperationalError` e pool esgotado, `src/goblinshift/runs/worker/coordinator.py:128`) que escapa de `coordinator.run` depois do claim. `_run_attempt` abre a pausa, registra o warning `claims pausados depois de falha de banco` e relança o erro (`src/goblinshift/entrypoints/worker.py:225-241`). O `logger.exception` da volta continua, e a pausa não engole nada.
- **Durante a pausa,** `_work_once` não reivindica e conta a volta como fila vazia (`src/goblinshift/entrypoints/worker.py:198-222`).
- **Janela derivada, não literal:** `orphan_cleanup_window` soma o lease (o da órfã vence no máximo um lease depois da falha), o intervalo da manutenção (que a encontra em até um ciclo) e a carência da parada do executor (`src/goblinshift/runs/worker/claim_pause.py:19-23`). Com os padrões de `Settings` (`worker_lease_s` 60 e `maintenance_interval_s` 30, `src/goblinshift/platform/settings.py:115,120`) e `STOP_GRACE` 10 s (`src/goblinshift/runs/worker/runner.py:46,49`), dá 100 s. `claim_pause_for` monta a janela a partir da configuração (`src/goblinshift/entrypoints/worker.py:164-171`).
- **Relógio monotônico injetável:** `ClaimPause` mede duração com `time.monotonic` por padrão. Uma falha nova reinicia a janela, e janela negativa levanta `ValueError` (`claim_pause.py:26-45`).
- **Evento:** o warning traz `run_id`, `attempt_id`, `error_type`, `sqlstate` e `pause_s`, sem a mensagem do banco, que traz host, porta e usuário.
- **Escopo mantido estreito,** decidido na revisão do PR #224: outras exceções não pausam. O escopo da decisão A é a falha transitória de banco, e as outras exceções já têm tratamento próprio.

O que não pausa, de propósito: falha no próprio claim (a transação reverteu, e não há tentativa deste worker), tentativa concluída, erro que não é falha transitória de banco e falha da renovação dentro de `supervise`, que continua absorvida.

## Consequências

- Um worker que perdeu o banco no meio de uma tentativa fica até 100 s sem reivindicar. Com um worker só, a fila inteira espera esse tempo. É o preço de não gastar claims de outra conta.
- **Limite: reinício do processo.** A pausa vive na memória. Um deploy ou SIGKILL dentro da janela a perde, e o processo novo pode reivindicar sobre a área suja. Decidido na revisão do PR #224: isso fica para um PR seguinte, com checagem por estado no `executor-ctl` antes do claim, que é mais robusta que pausar no boot (o que atrasaria a fila em 100 s a cada deploy). Até lá, um reinício dentro da janela ainda pode gastar um claim.
- **Limite: COMMIT ambíguo do claim.** Se o banco cair durante o COMMIT do claim, o servidor pode ter gravado a Attempt sem o worker saber. Não há pausa, e o caso continua como antes. É raro, e resolvê-lo exige reconciliar pelo nome da área.
- **Limite: janela aproximada.** Ela supõe que worker e manutenção leem o mesmo `worker_lease_s` e `maintenance_interval_s` da release, e não conta a duração do próprio ciclo da manutenção. Se a limpeza atrasar, o pior caso é um `work_area_dirty` depois da janela, que custa um claim, e não os quatro de antes.
- Outra exceção rara que escape de `coordinator.run` ainda pode gastar um claim com área suja. Ampliar o gatilho é uma linha, mas muda o escopo decidido.
- Não há evento de fim da pausa. Durante ela, o `serve` registra em debug "fila vazia; aguardando NOTIFY", o que é impreciso; o warning de início informa a duração.

## Compliance

- `tests/runs/test_claim_pause.py` (sem banco, relógio falso): janela de 100 s com os padrões; fórmula com lease, ciclo e carência; fronteira (ativa em 99,999 s, inativa em 100 s exatos); falha nova reinicia; janela negativa recusada; durante a pausa não há `claim_next`; falha no claim, tentativa concluída e `ProgrammingError` não pausam; o erro continua no `logger.exception`; os campos do evento.
- `tests/runs/test_claim_pause_after_db_failure.py` (integração com Postgres): nos três pontos (`record_start`, `_LeaseKeeper`, `finish`), a Run seguinte fica `queued` com `claim_count = 0` até 1 ms antes do fim da janela. Com janela zero, o defeito aparece (`work_area_dirty`).
- Sabotagens feitas no PR #224 (sem checar a pausa, sem `raise`, sem `begin`, pausar em qualquer `Exception`, `<=` na fronteira, janela sem a carência, janela negativa aceita, `record_start` absorvendo o erro, `stop_grace=0` no `serve`), todas reprovadas pela suíte.
