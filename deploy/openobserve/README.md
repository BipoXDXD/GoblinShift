# SLOs e alertas no OpenObserve (runbook)

SLOs e alertas de [3.7](../../docs/GoblinShiftPlan.md#s3-7) como código: as consultas em `sql/`, o catálogo em `catalog.py` e um provisionamento idempotente pela API do OpenObserve ([ADR 013](../../docs/adr/013-openobserve-telemetria.md)). Só biblioteca padrão: roda no notebook, pelo túnel SSH, ou no Python 3.12 da VPS.

## O que é provisionado

Todos os nomes começam com `gs-`. O que tem esse prefixo e saiu do catálogo é removido; o resto do OpenObserve não é tocado.

| Alerta | Consulta | Período / avaliação | Destino |
|---|---|---|---|
| `gs-slo-<slo>-aviso` | `queima.sql` sobre `<slo>.eventos.sql`, taxa 2x | 60 min / a cada 5 min | `gs-aviso` |
| `gs-slo-<slo>-urgente` | `queima.sql` sobre `<slo>.eventos.sql`, taxa 4x | 60 min / a cada 5 min | `gs-urgente` |
| `gs-causa-fila-cheia` | `causa-fila-cheia.sql` | 15 min / a cada 5 min | `gs-urgente` |
| `gs-causa-disco` | `causa-disco.sql` (stream de métricas) | 10 min / a cada 5 min | `gs-urgente` |
| `gs-causa-execucao-presa` | `causa-execucao-presa.sql` (span da manutenção) | 10 min / a cada 5 min | `gs-urgente` |
| `gs-causa-limitador-de-exclusoes` | `causa-limitador-de-exclusoes.sql` (span da manutenção) | 10 min / a cada 5 min | `gs-aviso` |
| `gs-causa-exclusao-de-conta-atrasada` | `causa-exclusao-de-conta-atrasada.sql` (span da manutenção) | 10 min / a cada 5 min | `gs-urgente` |
| `gs-causa-capacidade-global` | `causa-capacidade-global.sql` (span da manutenção) | 15 min / a cada 5 min | `gs-aviso` |
| `gs-causa-storage-sem-sentinela` | `causa-storage-sem-sentinela.sql` (span da manutenção) | 10 min / a cada 5 min | `gs-aviso` |
| `gs-causa-limitador-de-ausentes` | `causa-limitador-de-ausentes.sql` (span da manutenção) | 10 min / a cada 5 min | `gs-aviso` |
| `gs-causa-objetos-ausentes` | `causa-objetos-ausentes.sql` (span da manutenção) | 10 min / a cada 5 min | `gs-aviso` |
| `gs-causa-objetos-ilegiveis` | `causa-objetos-ilegiveis.sql` (span da manutenção) | 10 min / a cada 5 min | `gs-aviso` |
| `gs-causa-arquivos-nao-removidos` | `causa-arquivos-nao-removidos.sql` (span da manutenção) | 10 min / a cada 5 min | `gs-aviso` |
| `gs-causa-varredura-falhou` | `causa-varredura-falhou.sql` (span da manutenção, três voltas ou mais) | 10 min / a cada 5 min | `gs-aviso` |
| `gs-causa-ciclos-falhando` | `causa-ciclos-falhando.sql` (span da manutenção, três voltas ou mais) | 10 min / a cada 5 min | `gs-urgente` |
| `gs-causa-capacidade-divergente` | `causa-capacidade-divergente.sql` (contadores de eventos) | 10 min / a cada 5 min | `gs-aviso` |
| `gs-causa-sem-reserva` | `causa-sem-reserva.sql` (contadores de eventos) | 10 min / a cada 5 min | `gs-aviso` |
| `gs-causa-executor-nao-iniciou` | `causa-executor-nao-iniciou.sql` (span `attempt`, três tentativas ou mais) | 10 min / a cada 5 min | `gs-urgente` |
| `gs-causa-manutencao-sem-ciclo` | `causa-manutencao-sem-ciclo.sql` (ausência do span da manutenção) | 30 min / a cada 5 min | `gs-urgente` |
| `gs-causa-sem-traces` | `causa-sem-traces.sql` (ausência de eventos no stream) | 30 min / a cada 5 min | `gs-urgente` |
| `gs-heartbeat` | `heartbeat.sql`, sempre verdadeiro (stream de métricas) | 60 min / a cada 60 min | `gs-heartbeat` |

`<slo>` é `aceitacao` (meta 99,5%), `execucao` (99%) ou `download` (99,5%). Os critérios de elegível e bom estão comentados em cada `<slo>.eventos.sql`.

**Queima em duas janelas.** O alerta dispara quando a última hora **e** os últimos 15 minutos queimam o orçamento a 2x (aviso) ou 4x (urgente) o ritmo permitido. Com meta `1 - 1/N`, isso é `ruins * N >= taxa * elegíveis` em cada janela, em inteiros. Cada janela só conta com um mínimo de elegíveis (aceitação e download: 101 na hora e 26 nos 15 min; execução: 51 e 13), escolhido para que **uma falha isolada nunca dispare** (`tests/openobserve/test_catalog.py` confere isso para cada SLO e nível). Com pouco tráfego, a queima simplesmente não dispara: é o comportamento pedido em 3.7.

**Ausência.** `gs-causa-manutencao-sem-ciclo` e `gs-causa-sem-traces` disparam com a contagem em zero, ao contrário das outras causas. O heartbeat prova que o OpenObserve avalia alertas e alcança a internet; estes dois provam que os traces que os outros alertas leem continuam chegando. O período de 30 min passa da suspensão da manutenção pelas sondas da aceitação ([ADR 017](../../docs/adr/017-aceitacao-suspende-a-manutencao.md)).

**Silêncio.** Depois de notificar, o urgente fica quieto por 60 min e o aviso por 180 min; a fila cheia por 60 min e o disco por 240 min. Das outras causas: execução presa, exclusão atrasada, ciclos falhando, executor que não inicia e as duas de ausência, 60 min; limitador de exclusões, as cinco da varredura de ausentes, arquivos não removidos, capacidade divergente e sem reserva, 180 min; capacidade global, 240 min.

### O evento de término da Run

O SLO de execução lê o span `run.finished`, que o worker e a manutenção emitem quando uma Run chega a estado terminal (`goblinshift.runs.finished`): publicação, falha da receita, parada confirmada, `INFRA_POISON` no claim, `QUEUE_EXPIRED` e prazo vencido pela manutenção. A emissão acontece depois do commit e da liberação dos usos, uma vez por Run; se falhar, a Run não é afetada e o log registra `evento de término da execução perdido` com o `run_id`. A Run cancelada ainda na fila termina pela API e não emite o evento (não seria elegível de todo modo). Contrato, conferido por `tests/runs/test_run_finished_event.py` contra a consulta:

| Atributo | Valor |
|---|---|
| nome do span | `run.finished` (instantâneo, no momento da emissão) |
| `main` | `true` |
| `slo.execution.eligible` | `true` se a Run tem até 10 arquivos e 50 MiB de entrada e não foi cancelada pela pessoa usuária |
| `error.code` | código do catálogo de 1.6 no estado final, ausente em `succeeded` |
| `run.duration_ms` | do início da primeira tentativa até o estado terminal; ausente se nunca começou |
| `run.state` | estado final (`succeeded`, `failed`, `canceled`, …) |
| `run.attempts`, `run.queue_wait_ms`, `run.input_count` | claims, espera na fila até o primeiro claim (ausente se nunca começou) e número de entradas |
| `run_id`, `recipe_id`, `recipe_version`, `user.hash` | identificação, sem PII: a conta só pelo HMAC do `owner_id` |

### O evento por volta da manutenção

As causas da manutenção leem o span `maintenance.cycle` (`goblinshift.entrypoints.maintenance.run_cycles`), emitido a cada volta (30 s) com as contagens dos dois ciclos, o de execuções e arquivos e o de contas. Contrato, conferido por `tests/runs/test_maintenance_cycle_event.py` e `tests/runs/test_maintenance_history.py`:

| Atributo | Valor |
|---|---|
| nome do span | `maintenance.cycle`, do começo ao fim da volta |
| `main` | `true` |
| `maintenance.failed_cycles` | ciclos que falharam na volta (o span fica com status de erro; a mensagem só no log; causa `gs-causa-ciclos-falhando`) |
| `maintenance.runs_stuck_stopping` | Runs em `stopping` desde antes de 10 min (o primeiro entre o cancelamento e o prazo) |
| `maintenance.purge_limit_reached` | `1` se o limitador cortou o expurgo da volta, `0` se não |
| `maintenance.accounts_deletion_overdue` | contas com `DELETE /me` pedido há 20 h ou mais |
| `maintenance.storage_global_reserved_bytes`, `maintenance.storage_global_max_bytes` | o pool global do livro-razão (ADR 024) |
| `maintenance.runs_to_anonymize`, `maintenance.outputs_to_anonymize`, `maintenance.runs_to_delete`, `maintenance.idempotency_records_expired`, `maintenance.uploads_to_anonymize`, `maintenance.uploads_to_delete` | o que a retenção de 1.7 ainda deve: tamanho do acúmulo depois da volta |
| `maintenance.objects_missing_checked`, `maintenance.objects_missing_absent`, `maintenance.objects_missing_unreadable`, `maintenance.objects_marked_missing` | a varredura de objetos ausentes da volta: conferidos no disco, ausentes, ilegíveis (`stat` com erro que não é ausência; o objeto fica como está; causa `gs-causa-objetos-ilegiveis`) e marcados `missing` (causa `gs-causa-objetos-ausentes`) |
| `maintenance.missing_sweep_skipped` | `1` se faltou a sentinela de montagem e nada foi conferido (causa `gs-causa-storage-sem-sentinela`) |
| `maintenance.missing_governor_tripped` | `1` se o limitador travou o lote e nada foi marcado: mais da metade ausente, um lote pequeno (abaixo de 10) inteiro ausente a partir de dois, ou ilegível junto com ausente (causa `gs-causa-limitador-de-ausentes`) |
| `maintenance.missing_sweep_failed` | `1` se a varredura falhou na volta (log `varredura de ausentes falhou` com o traceback); o resto do ciclo segue (causa `gs-causa-varredura-falhou`) |
| `maintenance.purge_files_not_removed` | arquivos do expurgo e dos uploads incompletos que ficaram no disco na volta, depois da transação que os tirou de uso (causa `gs-causa-arquivos-nao-removidos`) |
| `maintenance.candidates_left_on_disk` | candidatos descartados que o disco recusou apagar na volta; cada um no log `arquivo não removido do disco`, com a chave (`alert = purge_file_not_removed`; causa `gs-causa-arquivos-nao-removidos`) |
| `maintenance.<etapa>` | o que cada etapa fez na volta (`runs_deleted`, `runs_anonymized`, `file_rows_deleted`, `objects_purged`, `leases_reconciled`, …) |
| `duration_ms` | duração da volta |

Sem PII: só contagens e bytes. As chaves de storage ficam só no log da manutenção.

### Contadores de eventos do log

Os logs dos containers não são exportados. Cada evento com o campo `alert` no log soma 1 no atributo `alert.<evento>` do span em curso (`goblinshift.platform.telemetry.AlertEventCounter`, ligado em `configure_logging`): o `maintenance.cycle` na manutenção, o evento amplo da requisição na API e o `attempt` no worker. Só o nome e a contagem saem; os IDs e as chaves ficam no log. Sem span gravando (uma CLI, por exemplo), o evento fica só no log.

| Atributo | Quem emite | Causa |
|---|---|---|
| `alert.capacity_divergence`, `alert.capacity_over_max` | manutenção | `gs-causa-capacidade-divergente` |
| `alert.upload_without_reservation` | API | `gs-causa-sem-reserva` |
| `alert.run_without_reservation` | worker | `gs-causa-sem-reserva` |
| `alert.purge_file_not_removed` | manutenção, API (cancelamento) e worker (fim da tentativa) | `gs-causa-arquivos-nao-removidos` |
| `alert.executor_start_failed` | worker (start recusado) | `gs-causa-executor-nao-iniciou` |

Os outros eventos `alert=` também somam, mas só são emitidos dentro da manutenção, e as causas deles leem as contagens do `maintenance.cycle`. `tests/openobserve/test_catalog.py` reprova evento sem alerta no catálogo e alerta que não lê o próprio evento.

Fora de span: a Run encerrada por `INFRA_POISON` no claim (`runs/worker/claim.py`) remove candidatos antes do span `attempt`, e uma falha nessa remoção fica só no log `arquivo não removido do disco` do worker.

### Roteiro das causas da manutenção

- **Execução presa em `stopping`.** O executor não confirmou a parada. Na VPS: `sudo systemctl status goblinshift-executor-ctl` e o log da manutenção (`executor sem inspeção` ou `executor não confirmou a parada`, com o `run_id` e o `attempt_id`). Com o `executor-ctl` de pé, `docker ps --filter name=gs-` mostra o container; a manutenção para e remove sozinha quando o Runner responde. Não apague linhas à mão: a Run termina pela reconciliação.
- **Limitador de exclusões.** Mais de 500 objetos ou 2 GiB pedidos para exclusão de uma vez. Legítimo depois de um `DELETE /me` grande ou de um dia de muito tráfego vencendo junto: as voltas seguintes terminam o serviço. Suspeito se não houver causa: pare a manutenção (`sudo docker stop goblinshift-maintenance-1`), investigue antes de deixar apagar e religue com `sudo docker start goblinshift-maintenance-1`.
- **Exclusão de conta atrasada.** A purga não termina em 20 h, quase sempre porque a conta tem Run não terminal (a purga espera) ou um ciclo de contas falhando (log `ciclo de manutenção falhou` com `cycle = contas`). Resolva antes das 24 h prometidas no aviso de privacidade.
- **Storage sem a sentinela** (`gs-causa-storage-sem-sentinela`). Falta `.goblinshift-storage` na raiz dos objetos, quase sempre o filesystem `/srv/goblinshift/storage` desmontado (`mountpoint /srv/goblinshift/storage`; o fstab usa `nofail`). Nada é marcado `missing`, mas a aplicação grava no disco do sistema. Monte o filesystem e reimplante. Só depois de conferir a montagem, a sentinela também pode ser gravada à mão com `deploy/storage-mark.sh` (leia o aviso de perigo no cabeçalho do script: não rode da cópia em `/srv/goblinshift/app`), que recusa sem montagem. Logo depois do primeiro deploy que trouxe a sentinela, o aviso para sozinho.
- **Limitador de ausentes** (`gs-causa-limitador-de-ausentes`). A varredura achou ausentes demais num lote e não marcou nada: o log `limitador de ausentes acionado` traz conferidos, ausentes e ilegíveis. Com ilegíveis, o disco devolve erro: veja `dmesg` e o filesystem antes de qualquer coisa. Sem ilegíveis, confira se o storage montado é o certo. Se é, e o banco foi restaurado de propósito de um backup mais antigo que os arquivos (3.8), desligue o limitador até a varredura dar a volta: defina `GS_MISSING_SWEEP_MAX_ABSENT_RATIO=1` como variável no recurso da aplicação no Coolify (o `deploy/coolify/app.json` a repassa à manutenção) e publique; erro de disco junto com ausente continua travando. Depois remova a variável e publique de novo para voltar ao normal.
- **Objetos marcados missing** (`gs-causa-objetos-ausentes`). Arquivos sumiram do storage montado, abaixo do limitador. Esperado depois de restaurar o banco; fora disso, investigue o que apagou arquivos fora do expurgo. `missing` é final, e a reserva do objeto já foi liberada.
- **Objetos ilegíveis** (`gs-causa-objetos-ilegiveis`). O `stat` falhou com erro que não é ausência; o objeto fica como está e a varredura volta nele na próxima passada. O log `objetos ilegíveis no storage` traz `errnos` (contagem por errno) e `sample` (até 5 chaves com o errno de cada). `EIO` indica o disco: veja `dmesg` e o filesystem antes de qualquer coisa, porque o storage não tem backup. `EACCES` indica dono ou permissão do caminho, quase sempre depois de mexer no filesystem à mão. Esta causa só vê erro no `stat` de objeto: sentinela ilegível derruba a varredura antes disso e aparece em `gs-causa-varredura-falhou`.
- **Arquivos não removidos** (`gs-causa-arquivos-nao-removidos`). A remoção falhou depois do commit, e nada repete a tentativa: o objeto do expurgo já está `deleted`, e o pendente (upload incompleto ou candidato) já perdeu a linha. O arquivo fica no disco e passa da retenção prometida. Cada falha está no log `arquivo não removido do disco`, com `storage_key` e o traceback. Corrija a causa (disco ou permissão) e apague cada chave à mão, conferindo antes que nenhuma linha fora de `deleted` em `stored_object` aponta para ela.
- **Capacidade global perto do teto.** As reservas passam de 85% dos 20 GiB do ambiente. Veja as contas que mais ocupam (`gs-admin usage --user <id>`; `gs-admin set-limits` ajusta o teto de uma conta) e o disco do storage; se o uso é legítimo, suba o teto do pool global com cuidado com o disco real.
- **Varredura falhando** (`gs-causa-varredura-falhou`). O log `varredura de ausentes falhou` traz o traceback. Erro de banco tende a passar sozinho; erro de leitura da sentinela indica o filesystem do storage, e aí vale o mesmo roteiro de objetos ilegíveis. O resto do ciclo continua rodando.
- **Ciclos falhando** (`gs-causa-ciclos-falhando`). O log `ciclo de manutenção falhou` traz `cycle` (`execuções` ou `contas`) e o traceback. Enquanto a falha durar, as contagens daquele ciclo faltam no span, então as causas que dependem delas não valem como sinal de que está tudo bem. Corrija a causa no banco ou no storage; a volta seguinte retoma sozinha. Publicar a infraestrutura com o banco fora por 90 s ou mais dispara este alerta (três voltas de 30 s): é esperado se ele parar sozinho com o banco de volta.
- **Capacidade divergente** (`gs-causa-capacidade-divergente`). Os logs `livro-razão de capacidade divergente` (`pools_corrected`, `objects_adopted`, `adoptions_refused`) e `livro-razão acima do teto do pool` trazem o tamanho. A reconciliação regrava o pool sozinha; o que pede investigação é a origem, porque uma reserva deixou de ser gravada ou liberada. Adoção recusada ou pool acima do teto voltam a cada volta até a capacidade ser ajustada.
- **Sem reserva** (`gs-causa-sem-reserva`). O log `upload sem reserva de capacidade` traz `upload_id` (API) e `publicação sem reserva de capacidade` traz `run_id` (worker). A operação seguiu e a reconciliação adota o objeto. Isso só cabe a upload ou Run anteriores à migração 0006; se o registro for mais novo, é defeito.
- **Executor não inicia** (`gs-causa-executor-nao-iniciou`). O `executor-ctl` recusou o start em três tentativas ou mais, e as Runs terminam em `INFRA_POISON`. O log `executor não iniciou` do worker traz `run_id` e, no campo `error`, o motivo (`docker_unavailable`, `image_not_allowed`, `work_area_dirty`, `work_dir_missing`, `unavailable`): leia o motivo antes de agir. O `executor-ctl` usa `--pull=never`: com `docker_unavailable`, confira primeiro se a imagem de `GS_EXECUTOR_IMAGE` continua no daemon, porque uma limpeza de imagens feita no host pode removê-la; uma nova release a recarrega antes de publicar. Com `work_area_dirty`, a imagem não é a causa: sobrou na área de trabalho algo de outra tentativa. O alerta conta esse motivo de propósito, porque a recusa gasta um claim como as outras e a Run é perdida de verdade em `INFRA_POISON`. Para verificar, compare as entradas citadas no `error` com `sudo ls -la /srv/goblinshift/exec-work` e `sudo docker ps --all --filter name=gs-`: a manutenção remove a área de tentativa encerrada ou sem registro na volta seguinte, e a de tentativa viva fica com o worker ou com a reconciliação de lease. O alerta depende de uma Run tentar iniciar: sem tráfego, a falta da imagem não aparece aqui.
- **Manutenção sem ciclo** (`gs-causa-manutencao-sem-ciclo`). Nenhuma volta terminou em 30 min. Se `gs-causa-sem-traces` não disparou junto, os traces chegam e o problema é a manutenção: `sudo docker ps --all --filter name=goblinshift-maintenance` e `sudo docker logs --tail 100 goblinshift-maintenance-1`. Container parado por uma aceitação interrompida à força volta com `sudo docker start goblinshift-maintenance-1` (ADR 017). Log parado no meio da volta com o container de pé é volta travada: registre o que ela fazia e reinicie o container.
- **Sem traces** (`gs-causa-sem-traces`). Nenhum evento chegou ao stream, e a manutenção sem ciclo costuma disparar junto. Comece pela exportação: `sudo docker logs --tail 100 goblinshift-otel-collector-1` e `OTEL_EXPORTER_OTLP_ENDPOINT` nos processos da aplicação. 503 repetido no log do Collector depois de um pico é o limite de ingestão descrito em "Atualizar o OpenObserve". Enquanto isso, os SLOs ficam sem dados, então confira o serviço pelos monitores externos.

### Span `attempt`

O worker emite um span `attempt` por tentativa (`goblinshift.runs.worker.attempt_event`), do início do coordenador até o desfecho, inclusive quando perde o fencing. É raiz do próprio trace e tem um *link* (não é filho) para o span da requisição de aceitação, a partir do `traceparent` gravado na Run; sem `traceparent` válido, fica sem link. É o evento para investigar uma tentativa, e `gs-causa-executor-nao-iniciou` lê dele o contador de start recusado. Contrato, conferido por `tests/runs/test_attempt_span.py`:

| Atributo | Valor |
|---|---|
| `main` | `true` |
| `run_id`, `attempt_id`, `attempt.number`, `attempt.generation`, `recipe_id`, `recipe_version`, `user.hash` | identificação, sem PII |
| `attempt.outcome` | estado da Run depois do desfecho (`succeeded`, `running` na retentativa, `failed`, …) ou `lost_fencing` |
| `error.code` | código do catálogo de 1.6 da Run, ausente sem falha |
| `executor.exit_code`, `executor.oom_killed`, `executor.timed_out`, `executor.enospc` | como o executor terminou; `enospc` é a saída 6 (disco da área de trabalho cheio, `RESOURCE_LIMIT_EXCEEDED`) |
| `inspect_ms`, `process_ms`, `package_ms` | fases medidas pelo executor (`telemetry.json`); ausentes se a fase não terminou ou se o arquivo fugiu do schema |
| `collect_ms`, `publish_ms` | coleta e gravação dos candidatos, e a transação de publicação, medidas pelo worker |
| `attempt.inputs`, `attempt.outputs`, `attempt.output_bytes`, `attempt.rejected` | entradas, saídas de conteúdo (quantidade e bytes) e rejeições do relatório; nunca nomes |
| `attempt.rejected.<CODE>` | rejeições por código, um atributo por código do catálogo fechado `ItemRejectionCode` presente no relatório |
| `queue_wait_ms` | da elegibilidade da Run (`eligible_at`, que na retentativa inclui o jitter) ao início da tentativa, pelo relógio do banco |
| `attempt.candidates_left_on_disk` | só se algum ficou: candidatos descartados que o disco recusou apagar no fim da tentativa; cada um no log `arquivo não removido do disco`, com a chave (`alert = purge_file_not_removed`) |
| `alert.executor_start_failed` | só se o `executor-ctl` recusou o start: `1` na tentativa (log `executor não iniciou`, com `alert = executor_start_failed`), ausente nos outros desfechos; lido por `gs-causa-executor-nao-iniciou` |
| `attempt.stop_reason` | razão de parada persistida quando a tentativa termina por parada (`CANCELED`, `DEADLINE_EXCEEDED`) |
| `attempt.cancel_latency_ms` | só no cancelamento: do `cancel_requested_at` até o coordenador confirmar a parada, pelo relógio do banco |
| `duration_ms` | duração da tentativa no worker |

### Fora do OpenObserve, por enquanto

| Causa de 3.7 | Onde fica |
|---|---|
| Backup diário que falhou | healthchecks.io (ping do `pg-backup.sh`) |
| Certificado TLS a menos de 14 dias | workflow tls-check + healthchecks.io (certificado TLS, 1 dia / 6 h) |
| Teste de aceitação do ambiente falhando | healthchecks.io (ping do `deploy/acceptance/scheduled.sh`, pelo timer semanal) |

### Monitores externos

O OpenObserve roda na própria VPS: se ela cair, ninguém avisa. Por isso estes monitores ficam fora dela (plano da fatia 8, 2.2 e 2.3). O healthchecks.io é passivo (recebe pings e avisa quando eles não chegam); o monitor de uptime é ativo (sonda a VPS de fora). As URLs de ping são segredo, porque quem as tem cala o alarme com pings falsos de sucesso: nenhuma fica no repositório.

| Monitor | Serviço | Período / tolerância | Quem pinga ou o que sonda | Onde fica a URL |
|---|---|---|---|---|
| `backup diário` | healthchecks.io | 1 dia / 2 h | `deploy/backup/pg-backup.sh` (`/start`, sucesso, `/fail`) | `BACKUP_HEALTHCHECK_URL` em `/srv/goblinshift/secrets/backup.env` |
| `restauração mensal` | healthchecks.io | 31 dias / 1 dia | workflow "Restauração testada do backup" | secret `RESTORE_HEALTHCHECK_URL` (environment `backup-restore`) |
| `alertas do OpenObserve` | healthchecks.io | 1 h / 30 min | alerta `gs-heartbeat` (prova que o OpenObserve avalia e alcança a internet) | `GS_ALERT_HEARTBEAT_URL` em `~/.config/goblinshift/alertas.env` |
| `aceitação semanal` | healthchecks.io | 7 dias / 2 h | `deploy/acceptance/scheduled.sh` pelo `goblinshift-acceptance.timer` (`/start`, sucesso, `/fail`) | `ACCEPTANCE_HEALTHCHECK_URL` em `/etc/goblinshift/acceptance.env` (root, `chmod 600`) |
| `certificado TLS` | healthchecks.io | 1 dia / 6 h | workflow `tls-check` (`deploy/tls_check`: cadeia, hostname e 14 dias ou mais; sucesso ou `/fail`) | secret de repositório `TLS_HEALTHCHECK_URL` |
| `jornada com escrita` | healthchecks.io | 7 dias / 1 dia | workflow `e2e-producao` (upload, execução e download em produção depois de cada release e toda quarta; sucesso ou `/fail`, e nada quando a rodada é pulada por falta de cota) | secret de repositório `E2E_WRITE_HEALTHCHECK_URL` |
| `/readyz` | Worker `goblinshift-uptime` da Cloudflare | 2 min; alerta depois de 2 falhas | `GET /readyz` esperando 200, com aviso direto no Discord ([deploy/uptime](../uptime/README.md)) | secret `DISCORD_WEBHOOK_URL` do Worker |

A tolerância de 2 h dos checks do backup e da aceitação cobre o sorteio de até 30 min dos timers (`RandomizedDelaySec`) e a até 1 h de teste. A aceitação semanal para o worker por cerca de 10 a 30 minutos, no domingo de madrugada (07:30 UTC mais o sorteio, depois da janela do backup): as execuções aceitas nesse intervalo esperam na fila. Detalhes e instalação no README da raiz, "Teste de aceitação do ambiente".

## Segredos

Nada de credencial ou URL de destino na linha de comando: tudo vem de arquivos no formato `.env` com `chmod 600` (o script recusa arquivo legível por outros). `--secrets-file` é repetível; numa chave repetida, vale o último arquivo.

| Chave | Obrigatória | Conteúdo |
|---|---|---|
| `OPENOBSERVE_ROOT_EMAIL` | sim | usuário do OpenObserve; na VPS já está em `/srv/goblinshift/secrets/app.env` |
| `OPENOBSERVE_ROOT_PASSWORD` | sim | senha dele, no mesmo arquivo |
| `GS_ALERT_URGENT_URL` | sim | webhook HTTPS do canal urgente |
| `GS_ALERT_WARNING_URL` | não | webhook HTTPS dos avisos; sem ela, os avisos vão para o urgente |
| `GS_ALERT_HEARTBEAT_URL` | sim | URL de ping de um check do healthchecks.io |

As URLs precisam ser HTTPS e públicas: o OpenObserve recusa destino em IP privado (ADR 013), e a URL de um webhook é segredo, porque quem a tem publica no canal. Nenhuma mensagem do script mostra essas URLs.

### Opções de canal

Todo destino é um webhook `POST` com corpo JSON `{"text": "...", "content": "...", "alert": "..."}`, em que `text` e `content` trazem `[GoblinShift] <alerta>: <descrição>`.

| Canal | URL | Observação |
|---|---|---|
| **ntfy.sh** (push no celular, sem conta) | `https://ntfy.sh/<tópico-longo-e-aleatório>` | A notificação mostra o JSON inteiro. O tópico é a senha: use um nome impossível de adivinhar |
| **Discord** | `https://discord.com/api/webhooks/<id>/<token>` | Lê `content` |
| **Telegram** | `https://api.telegram.org/bot<token>/sendMessage?chat_id=<id>` | Lê `text` |
| **Slack** | `https://hooks.slack.com/services/...` | Lê `text` |
| **healthchecks.io como canal** | `https://hc-ping.com/<uuid>/fail` | Cada alerta marca o check como falho, e o healthchecks.io avisa pelas integrações dele (e-mail, Telegram, app). O check volta a "up" só com um ping de sucesso manual |
| **E-mail** | — | O OpenObserve envia e-mail só com SMTP configurado (`ZO_SMTP_*` no Compose, com um provedor como Resend, Brevo ou Amazon SES). Não está implementado aqui |

Recomendação de 3.7 e do plano das fatias 7–8: ntfy ou Telegram/Discord para o urgente; o mesmo canal ou um segundo, menos barulhento, para os avisos.

**Heartbeat.** Crie no healthchecks.io um check "alertas do OpenObserve" com período de 1 hora e tolerância de 30 minutos, e use a URL de ping dele (sem `/fail`) em `GS_ALERT_HEARTBEAT_URL`. Se o OpenObserve parar de avaliar alertas, o ping some e o healthchecks.io avisa por fora da VPS. O heartbeat lê as métricas de disco, que chegam a cada minuto mesmo numa hora sem nenhuma requisição.

## Provisionar pelo túnel SSH

O OpenObserve só escuta em `127.0.0.1:5080` da VPS. No notebook:

```bash
# 1. Segredos locais (uma vez), fora do repositório, legíveis só por você
install -d -m 700 ~/.config/goblinshift
$EDITOR ~/.config/goblinshift/alertas.env      # as chaves da tabela acima
chmod 600 ~/.config/goblinshift/alertas.env

# 2. Túnel, num terminal à parte (fica aberto)
ssh -N -L 5080:127.0.0.1:5080 <usuário>@<vps>

# 3. Provisionamento (idempotente: pode rodar de novo a cada mudança no catálogo)
cd deploy && uv run python -m openobserve provision --secrets-file ~/.config/goblinshift/alertas.env
```

A saída lista cada template, destino e alerta criado, atualizado ou removido. Antes de mexer em qualquer alerta, o script valida todas as consultas no OpenObserve; se alguma for recusada, sai com 1 e não altera alertas nem destinos.

Na primeira vez num stream sem as colunas dos SLOs (antes da primeira Run terminada depois do deploy, por exemplo), o script grava um span-semente, `goblinshift.schema-seed` com `main = false`, que declara as colunas. Ele não conta em nenhum SLO, porque todas as consultas filtram `main = 'true'`; sem ele, o OpenObserve recusaria a consulta em toda avaliação.

Também dá para rodar na própria VPS, sem túnel, a partir do [checkout temporário](../../README.md#checkout-temporário-na-vps) (nunca da cópia parada em `/srv/goblinshift/app`), como o usuário `goblinshift` (dono de `/srv/goblinshift/secrets`, modo 700), somando o `app.env` (credencial) a um arquivo só com as URLs, também com `chmod 600`:

```bash
cd "$gs_tmp/repo/deploy"
sudo -u goblinshift python3 -B -m openobserve provision \
  --secrets-file /srv/goblinshift/secrets/app.env --secrets-file /srv/goblinshift/secrets/alertas.env
```

**Disco e heartbeat.** O alerta de disco e o heartbeat leem `system_filesystem_utilization`, que o Collector produz com as montagens do `compose.prod.yaml`. O provisionamento falha enquanto esse stream não existir: confira que a publicação com o Collector novo já rodou. Depois de mudar só o `docker/otel-collector/config.yaml`, publique o recurso de infraestrutura no Coolify e rode `sudo docker restart goblinshift-otel-collector-1` na VPS.

## Relatório do SLI e do orçamento

```bash
cd deploy && uv run python -m openobserve sli --secrets-file ~/.config/goblinshift/alertas.env
```

Mostra, para os últimos 30 dias, elegíveis, bons, SLI, meta e orçamento restante de cada SLO. Com algum orçamento esgotado, a última linha diz `Orçamento ESGOTADO`: pela política de 3.7, a próxima fatia não começa.

## Provocar os alertas (critério de pronto da fatia 8)

O critério é "um alerta de queima e um de causa dispararam num teste provocado". Os dois roteiros abaixo rodam na VPS **antes da abertura**: o primeiro gasta de verdade o orçamento de aceitação, que só se recupera depois de 30 dias de janela.

Antes, confira que o check do heartbeat no healthchecks.io recebeu o primeiro ping (até 1 hora depois do provisionamento): é a prova de que o OpenObserve avalia os alertas e alcança a internet.

### Queima: aceitação com o banco parado

Com o PostgreSQL parado, a API responde 500 a todo `POST /runs` (a credencial é conferida no banco), e cada resposta é um evento elegível e ruim. São precisos pelo menos 101 na hora e 26 nos 15 min.

```bash
# Direto no container, nunca pela cópia parada em /srv/goblinshift/app (README, "Comandos manuais na VPS").
sudo docker stop goblinshift-postgres-1
# Chave no formato válido, mas inexistente: nada é criado.
key="gsk_0000000000000000_$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')"
for i in $(seq 130); do
  curl -s -o /dev/null -w '%{http_code}\n' -X POST <endereço público>/api/v1/runs \
    -H "Authorization: Bearer $key" -H "Idempotency-Key: drill-$(printf '%016d' "$i")" \
    -H 'Content-Type: application/json' -d '{}'
done | sort | uniq -c        # esperado: 130 respostas 500
sudo docker start goblinshift-postgres-1
```

Em até 10 minutos (exportação do Collector mais a avaliação a cada 5 min) chegam `gs-slo-aceitacao-urgente` (canal urgente) e `gs-slo-aceitacao-aviso` (canal de aviso). Se as respostas não forem 500 (por exemplo, 429 do limitador do Nginx), elas não contam: ajuste e repita.

### Causa: disco da telemetria acima de 85%

O filesystem da telemetria tem 10 GiB; um arquivo de preenchimento leva a ocupação para 90%. Encher a telemetria para a ingestão só em 100%, então 90% é seguro.

```bash
fs=/srv/goblinshift/telemetry
size=$(df --output=size -B1 "$fs" | tail -1); used=$(df --output=used -B1 "$fs" | tail -1)
sudo fallocate -l $(( size * 90 / 100 - used )) "$fs/drill-fill"
df -h "$fs"                  # acima de 85%
# esperar a notificação de gs-causa-disco no canal urgente: até ~15 min (coleta de 60 s,
# 10 min acima de 85% sem interrupção, avaliação a cada 5 min)
sudo rm "$fs/drill-fill"
```

Registre as duas notificações (horário e canal) no plano ao fechar a fatia.

## Atualizar o OpenObserve

O recurso "GoblinShift - Banco e observabilidade" do Coolify (`deploy/coolify/infra.json`) tem o Postgres, o Collector e o OpenObserve. A release da CI não o publica: o workflow e o receptor só trocam imagens do recurso da aplicação ([release/README.md](../coolify/release/README.md)). A imagem é fixada por digest e tem `pull_policy: never`, então ela precisa chegar à VPS antes da publicação.

A publicação termina em `docker compose up -d`, que por si só recria apenas o serviço cujo hash de configuração mudou; o Coolify, porém, remove todos antes (abaixo). Por isso esta troca tem duas etapas. O recurso foi publicado pela última vez no commit `ff88bebb`, e depois dele o `infra.json` ganhou os tetos de memória, CPU e processos dos três serviços e os buffers do OpenObserve ([ADR 013](../../docs/adr/013-openobserve-telemetria.md#teto-de-memória-2026-10-28)). Esses acréscimos mudam o hash dos três, então publicar a v1.1.2 direto daquele commit recriaria também o Postgres.

1. **[Primeira etapa](#primeira-etapa-publicar-os-limites), à parte:** publicar a `main` ainda com a v1.0.4, só para aplicar os limites. Ela recria os três serviços, com a queda do banco que isso traz, e não migra nada.
2. **Segunda etapa, o resto desta seção:** trocar a imagem. Daí em diante a única diferença é a linha `image` do OpenObserve, mas a publicação pelo Coolify ainda recria os três serviços (abaixo).

Ensaiado com o Compose derivado do `infra.json`: do `ff88bebb` para o PR, o plano recria os três; da `main` com limites para o PR, no Compose puro o Postgres e o Collector ficam `Running`, com o mesmo `StartedAt` e o mesmo hash. Os rótulos que o próprio Coolify acrescenta não entraram no ensaio, por isso o passo 4 anota o estado dos dois e a conferência o compara.

**A publicação pelo Coolify derruba o recurso inteiro.** Em 2026-11-05, a primeira etapa mostrou duas coisas que o ensaio com Compose não cobriu:

- O Coolify remove os containers do recurso ("Removing old containers") antes de subir os novos, mesmo os que não mudaram. O Postgres, o Collector e o OpenObserve caem em toda publicação, e a API fica sem banco até eles voltarem.
- Os três serviços usam `pull_policy: never`. A imagem de um container em execução pode não estar disponível depois que ele é removido: `postgres:18.6-trixie` sumiu, a publicação falhou com os containers já removidos, e a produção ficou cerca de 4 minutos fora, sem perda de dados, até `docker pull` da imagem fixada e nova publicação.

Por isso, antes de **toda** publicação deste recurso, confira na VPS que cada imagem fixada no commit a publicar existe no disco, e baixe a que faltar:

```bash
git show <commit>:deploy/coolify/infra.json | jq -r '.services[].image' | while read -r imagem; do
  sudo docker image inspect "$imagem" --format '{{.Id}}' >/dev/null 2>&1 || sudo docker pull -q "$imagem"
  sudo docker image inspect "$imagem" --format "ok $imagem" || echo "FALTA $imagem"
done
```

Qualquer `FALTA` impede a publicação. Durante a queda, o worker reinicia sozinho até o Postgres voltar.

**Nunca use Stop nem Restart do recurso no painel:** eles agem sobre o recurso inteiro, inclusive o Postgres. Pare só o container do OpenObserve, como abaixo.

### Primeira etapa: publicar os limites

Tudo na VPS, em janela própria: o Postgres é recriado, e a aplicação fica sem banco por instantes. Sem Run em andamento e sem release da CI rodando, porque os receptores não coordenam uma publicação manual concorrente ([ISOLATION.md](../coolify/release/ISOLATION.md)). O UUID do recurso aparece na URL dele no painel. Estas definições servem para a seção inteira; numa sessão nova, repita o bloco.

```bash
UUID=<uuid do recurso "GoblinShift - Banco e observabilidade">
TOKEN=$(sudo cat /etc/coolify-release/api-token)
API=http://127.0.0.1:8000/api/v1/applications/$UUID

publicar() {
  local deployment status
  deployment=$(curl -fsS -X POST -H "Authorization: Bearer $TOKEN" "$API/start" | jq -r .deployment_uuid)
  while :; do
    status=$(curl -fsS -H "Authorization: Bearer $TOKEN" \
      "http://127.0.0.1:8000/api/v1/deployments/$deployment" | jq -r .status)
    echo "$status"
    case "$status" in
      queued|in_progress) sleep 5 ;;
      finished) return 0 ;;
      *) return 1 ;;
    esac
  done
}

estado_dos_servicos() {
  sudo docker inspect -f '{{.Name}} {{.State.StartedAt}} {{index .Config.Labels "com.docker.compose.config-hash"}}' \
    goblinshift-postgres-1 goblinshift-otel-collector-1 goblinshift-openobserve-1
}
```

1. **Escolha o commit e confira.** `ALVO` é um commit da `main` que ainda fixa a v1.0.4, ou seja, anterior ao merge do PR da v1.1.2. Num clone, o `git show` precisa mostrar a v1.0.4, e o `git diff` só os tetos dos três serviços e as três variáveis `ZO_MEM*`.

   ```bash
   git show <ALVO>:deploy/coolify/infra.json | grep 'openobserve/openobserve:'
   git diff ff88bebb <ALVO> -- deploy/coolify/infra.json docker/otel-collector/config.yaml
   ```

2. **Renove o backup do banco e grave o estado.** Os três arquivos ficam em `/var/backups` para sobreviver a uma sessão nova. O horário de início é a referência do `StartedAt` no pré-requisito da segunda etapa.

   ```bash
   sudo systemctl start goblinshift-pg-backup.service
   sudo journalctl -u goblinshift-pg-backup -n 5 --no-pager
   estado_dos_servicos | sudo tee /var/backups/goblinshift-o2-hash-antes.txt
   curl -fsS -H "Authorization: Bearer $TOKEN" "$API" | jq -r .git_commit_sha \
     | sudo tee /var/backups/goblinshift-o2-etapa1-commit-anterior.txt
   date -u +%Y-%m-%dT%H:%M:%SZ | sudo tee /var/backups/goblinshift-o2-etapa1-inicio.txt
   ```

3. **Publique.** Com o `PATCH`, o recurso fica num commit fixo, e o auto-deploy deixa de importar para o que ele vai publicar.

   ```bash
   ALVO=<commit completo>
   curl -fsS -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d "{\"git_commit_sha\":\"$ALVO\"}" "$API"
   publicar    # precisa terminar em finished
   ```

4. **Confira a saúde.** Os três serviços `running`, com o Postgres `healthy`; a API, o worker e a manutenção `healthy`; `/readyz` em 200; e, dois minutos depois, o Collector sem falha de exportação. Depois rode o `conferir_etapa1` do [pré-requisito](#antes-do-merge).

   ```bash
   sudo docker inspect -f '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} reinícios={{.RestartCount}}' \
     goblinshift-postgres-1 goblinshift-otel-collector-1 goblinshift-openobserve-1 \
     goblinshift-api-1 goblinshift-worker-1 goblinshift-maintenance-1
   sudo docker exec goblinshift-postgres-1 pg_isready -U goblinshift -d goblinshift
   curl -fsS -o /dev/null -w '%{http_code}\n' https://goblinshift.dev/readyz
   sudo docker logs --since 2m goblinshift-otel-collector-1 2>&1 | grep -c 'Exporting failed'    # 0
   ```

**Rollback da primeira etapa.** Rode antes a conferência de imagens do início da seção. Esta etapa não migra nada: os dados continuam nos mesmos binds, e a v1.0.4 lê o mesmo volume. Voltar é publicar o commit gravado no passo 2, o que recria os três serviços de novo, sem os tetos. Quem aplicou os limites ao vivo precisa reaplicá-los depois. Se o valor gravado for `HEAD`, a função recusa, porque esse valor apontaria para a `main`: grave à mão o commit fixo `ff88bebb` completo no arquivo e rode de novo. O backup do passo 2 só entra se o banco voltar com defeito.

```bash
rollback_etapa1() {
  local anterior
  anterior=$(sudo cat /var/backups/goblinshift-o2-etapa1-commit-anterior.txt) || return 1
  [[ "$anterior" =~ ^[0-9a-f]{7,40}$ ]] || { echo "commit fixo exigido, achei: $anterior"; return 1; }
  curl -fsS -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"git_commit_sha\":\"$anterior\"}" "$API" || return 1
  publicar
}
rollback_etapa1
```

### De v1.0.4 para v1.1.2

Motivo: o crítico [GHSA-7w4v-2c9r-q94v](https://github.com/openobserve/openobserve/security/advisories/GHSA-7w4v-2c9r-q94v) (CVSS 9,8, senha externa vazia aceita no login) e os demais [advisories](https://github.com/openobserve/openobserve/security/advisories) corrigidos até a v1.1.1. A v1.1.2 é a última estável em 2026-11-05; a v1.2.0-rc1 ainda é candidata. Fontes: notas da [v1.1.0](https://github.com/openobserve/openobserve/releases/tag/v1.1.0), da [v1.1.1](https://github.com/openobserve/openobserve/releases/tag/v1.1.1) e da [v1.1.2](https://github.com/openobserve/openobserve/releases/tag/v1.1.2), PRs [#15054](https://github.com/openobserve/openobserve/pull/15054), [#15191](https://github.com/openobserve/openobserve/pull/15191) e [#15087](https://github.com/openobserve/openobserve/pull/15087), e o diff de `src/config/src/config.rs` e `src/config/src/meta/alerts/alert.rs` entre as tags.

| Tema | O que muda | Efeito aqui |
|---|---|---|
| Metadados | 36 migrações novas no SQLite (`DB_SCHEMA_VERSION` de 77 para 97), quase todas tabelas e colunas de recursos novos | Aplicadas em menos de 1 s no ensaio. **Sem volta**, veja [Rollback](#rollback) |
| API de alertas | `/api/v2/{org}/alerts` igual; a resposta ganha `keep_firing_for` (0), `notify_on_recovery` (`false`) e `recovery_destinations` (`[]`) | O cliente e o `provision` não mudaram. Os defaults mantêm o comportamento atual: sem aviso de recuperação |
| Destinos e templates | Sem mudança de contrato. Destinos passam pelo cliente com proteção contra SSRF, com novas allowlists `ZO_SSRF_ALLOWED_CIDRS` e `ZO_SSRF_ALLOWED_HOSTS` | Nenhum, porque os destinos são URLs públicas HTTPS. Entrega recusada agora conta como falha no histórico do alerta (#15154) |
| Autenticação | Senha vazia ou conta sem `password_ext` não entram mais com Basic, presigned ou `auth_ext` | Só contas externas. O root continua: o Collector seguiu gravando métricas depois da troca |
| Variáveis | `ZO_METRICS_INDEX_ENABLED` e `ZO_FEATURE_WAL_PACK_ENABLED` saíram. Default de `ZO_EXT_AUTH_SALT` ficou vazio | Nenhuma das duas está no `infra.json`. Instalação existente mantém o salt legado e registra um aviso |
| Consultas | Motor DataFusion 55.1; PromQL igual ao Prometheus | Os alertas usam SQL, e a suíte `-m openobserve` passou inteira sem mudança de código |
| Imagem | 549 MB para 594 MB em arm64 | O `docker pull` da VPS leva mais alguns MB. A imagem da v1.0.4 não tem shell, então o backup roda no host |

A partida da v1.1.2 registra dois avisos esperados: `ZO_EXT_AUTH_SALT is not set` e `ZO_INTERNAL_GRPC_TOKEN is not set`. Ficam como estão nesta troca: definir o salt numa instalação existente muda a derivação das credenciais externas e é uma decisão à parte.

### Antes do merge

Tudo na VPS, com as definições da [primeira etapa](#primeira-etapa-publicar-os-limites) na sessão.

```bash
NEW='openobserve/openobserve:v1.1.2@sha256:4a154849432f75c4fb024de32fe34163c02406c905dab22bf8d213d2e3386392'
OLD='openobserve/openobserve:v1.0.4@sha256:d4a878fac1f6c56003764f7f2a1625668917388f167e222c8c810de3f54c56ba'
```

**Pré-requisito: a primeira etapa já publicada.** Sem ela, a publicação abaixo muda o hash do Postgres e do Collector junto com a versão do OpenObserve, misturando as duas causas se algo falhar. Limites no `docker inspect` não provam isso: em produção eles já foram aplicados ao vivo, e o hash de configuração continua o de `ff88bebb`. O critério são o hash e o `StartedAt` dos três serviços: o hash precisa ser diferente do gravado em `/var/backups/goblinshift-o2-hash-antes.txt`, e o `StartedAt` precisa ser posterior ao início da primeira etapa, gravado em `/var/backups/goblinshift-o2-etapa1-inicio.txt`. Sem esses arquivos, a conferência falha: o hash não pode ser comparado com um calculado do repositório, porque inclui os rótulos do Coolify.

```bash
conferir_etapa1() {
  local inicio nome iniciado hash antes falhas=0
  inicio=$(sudo cat /var/backups/goblinshift-o2-etapa1-inicio.txt) || return 1
  while read -r nome iniciado hash; do
    antes=$(sudo awk -v nome="$nome" '$1 == nome {print $3}' /var/backups/goblinshift-o2-hash-antes.txt)
    if [ -n "$antes" ] && [ -n "$hash" ] && [ "$hash" != "$antes" ] && [[ "$iniciado" > "$inicio" ]]; then
      echo "ok $nome"
    else
      echo "FALHOU $nome"
      falhas=1
    fi
  done < <(estado_dos_servicos)
  return "$falhas"
}
conferir_etapa1
```

Se aparecer `FALHOU`, pare: publique a primeira etapa antes do merge deste PR. Como complemento, confira os limites e as variáveis:

```bash
sudo docker inspect -f '{{.Name}} {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}} {{.HostConfig.PidsLimit}}' \
  goblinshift-postgres-1 goblinshift-otel-collector-1 goblinshift-openobserve-1
# Só as variáveis de memória: o ambiente completo do container tem a senha do root.
sudo docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' goblinshift-openobserve-1 \
  | grep -E '^ZO_(MEMORY_CACHE_MAX_SIZE|MEMORY_CACHE_DATAFUSION_MAX_SIZE|MEM_TABLE_MAX_SIZE)='
```

| Container | Memória | `NanoCpus` | Processos |
|---|---|---|---|
| `goblinshift-postgres-1` | 536870912 | 1000000000 | 256 |
| `goblinshift-otel-collector-1` | 268435456 | 250000000 | 64 |
| `goblinshift-openobserve-1` | 536870912 | 500000000 | 128 |

As variáveis precisam sair como `ZO_MEMORY_CACHE_MAX_SIZE=64`, `ZO_MEMORY_CACHE_DATAFUSION_MAX_SIZE=128` e `ZO_MEM_TABLE_MAX_SIZE=64`.

1. **Auto-deploy desligado.** Com ele ligado, o merge publica sem backup. Rode a leitura abaixo e confira `is_auto_deploy_enabled` em `settings`. Se estiver `true`, desligue antes do merge, com o mesmo `PATCH` do [ADR 041](../../docs/adr/041-ip-do-cliente-atras-da-cloudflare.md#antes-do-merge). Se não der para saber, não faça o merge. A ordem importa: sem a imagem na VPS, uma publicação falha sem tocar no container atual (ensaiado com o Compose local), mas o passo 2 tira essa proteção.

   Guarde em arquivo o `git_commit_sha`: é o destino do rollback, e precisa sobreviver a uma sessão nova. Ele precisa ser um commit fixo, o `ALVO` da primeira etapa. Se for `HEAD`, pare: o rollback recusa esse valor.

   ```bash
   curl -fsS -H "Authorization: Bearer $TOKEN" "$API" \
     | jq '{git_branch, git_commit_sha, docker_compose_location, watch_paths, settings}'
   curl -fsS -H "Authorization: Bearer $TOKEN" "$API" | jq -r .git_commit_sha \
     | sudo tee /var/backups/goblinshift-o2-commit-anterior.txt
   ```

   Num clone do repositório, a diferença entre esse commit e o que será publicado precisa ser só a linha `image` do OpenObserve. Qualquer outra linha muda o hash de outro serviço:

   ```bash
   git diff <commit anotado> <commit a publicar> -- deploy/coolify/infra.json docker/otel-collector/config.yaml
   ```

2. **Imagem na VPS, e a antiga preservada.** Não rode `docker image prune` até encerrar a janela de rollback.

   ```bash
   sudo docker pull "$NEW"
   sudo docker image inspect "$NEW" --format '{{.Os}}/{{.Architecture}}'   # linux/amd64
   sudo docker image inspect "$OLD" --format '{{.Id}}'                     # precisa existir
   ```

3. **Espaço para o backup, em outro filesystem.** A telemetria tem 10 GiB fixos e é o que está sendo salvo, então o backup não pode ficar nela. As duas linhas do `df --output=target` precisam ser diferentes; iguais significam que `/srv/goblinshift/telemetry` não está montado ou que `/var/backups` foi parar nele.

   ```bash
   df --output=target /var/backups /srv/goblinshift/telemetry
   sudo du -sh /srv/goblinshift/telemetry/data
   df -h /var/backups
   ```

### Publicação

Depois do merge e com a release da CI terminada, porque os receptores não coordenam uma publicação manual concorrente ([ISOLATION.md](../coolify/release/ISOLATION.md)). Fora de horário de uso e sem Run em andamento: durante a troca os alertas ficam sem avaliação, e o Collector só segura o que chegar enquanto durarem as novas tentativas dele.

4. **Anote o estado e renove o backup do banco.** Com o pré-requisito, o hash do Postgres e do Collector não muda, mas o Coolify os recria mesmo assim, e a aplicação cai por instantes. Confira antes as imagens, como no início desta seção.

   ```bash
   sudo docker inspect -f '{{.Name}} {{.State.StartedAt}} {{index .Config.Labels "com.docker.compose.config-hash"}}' \
     goblinshift-postgres-1 goblinshift-otel-collector-1 | tee ~/o2-antes.txt
   sudo systemctl start goblinshift-pg-backup.service
   sudo journalctl -u goblinshift-pg-backup -n 5 --no-pager
   ```

5. **Pare só o OpenObserve e salve o volume.** Com ele de pé, o SQLite e o WAL dão uma cópia inconsistente. O `-S` é obrigatório: cada WAL é um arquivo esparso de 512 MiB, e sem ele a restauração ocupa 2,6 GiB onde o volume tinha 65 MiB (medido), num filesystem de 10 GiB. O arquivo tem as URLs dos destinos, que são segredo: fica só leitura do root, e sai da VPS só cifrado. O arquivo `.sha256` guarda o caminho e a soma juntos, para o rollback funcionar numa sessão nova.

   ```bash
   sudo docker stop -t 60 goblinshift-openobserve-1
   backup=/var/backups/goblinshift-telemetry-v1.0.4-$(date -u +%Y%m%dT%H%M%SZ).tar.gz
   sudo sh -c "umask 077 && tar -C /srv/goblinshift/telemetry --numeric-owner -czSf '$backup' data"
   sudo tar -tzf "$backup" | grep '^data/db/metadata.sqlite'      # o banco precisa aparecer
   sudo sha256sum "$backup" | sudo tee /var/backups/goblinshift-o2-backup.sha256
   sudo sha256sum -c /var/backups/goblinshift-o2-backup.sha256
   ```

6. **Publique.** O recurso está no commit fixo do passo 1, então aponte antes para o commit de merge: `curl -fsS -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"git_commit_sha":"<commit de merge>"}' "$API"`.

   ```bash
   publicar    # precisa terminar em finished
   ```

### Conferência

```bash
sudo docker inspect -f '{{.Config.Image}} {{.State.Status}} reinícios={{.RestartCount}}' goblinshift-openobserve-1
sudo docker inspect -f '{{.Name}} {{.State.StartedAt}} {{index .Config.Labels "com.docker.compose.config-hash"}}' \
  goblinshift-postgres-1 goblinshift-otel-collector-1 | awk '{print $1, $3}' | diff - <(awk '{print $1, $3}' ~/o2-antes.txt) && echo 'mesmo hash'
sudo docker exec goblinshift-postgres-1 pg_isready -U goblinshift -d goblinshift
sudo docker logs goblinshift-openobserve-1 2>&1 | grep -E 'DB_SCHEMA|DB upgrade completed'
sudo docker logs goblinshift-openobserve-1 2>&1 | grep -c 'has been applied'    # 36
sudo docker logs --since 2m goblinshift-otel-collector-1 2>&1 | grep -c 'Exporting failed'   # 0, dois minutos depois da partida
sudo docker stats --no-stream goblinshift-openobserve-1
curl -fsS -o /dev/null -w '%{http_code}\n' https://goblinshift.dev/readyz    # 200
```

- **Imagem:** a nova, `running`, zero reinícios. Log com `DB upgrade completed to version 97`.
- **16 alertas:** rode o `provision` (seção [Provisionar pelo túnel SSH](#provisionar-pelo-túnel-ssh)) e conte. O script é idempotente, então só confirma o que já existe: 16 linhas `alerta atualizado`, nenhuma `alerta criado`, nenhuma `alerta removido`, e sai com 0.

  ```bash
  cd deploy && uv run python -m openobserve provision --secrets-file ~/.config/goblinshift/alertas.env | tee /tmp/o2-provision.txt
  grep -c '^alerta atualizado' /tmp/o2-provision.txt    # 16
  grep -E '^alerta (criado|removido)' /tmp/o2-provision.txt    # nada
  rm /tmp/o2-provision.txt
  uv run python -m openobserve sli --secrets-file ~/.config/goblinshift/alertas.env
  ```

- **Heartbeat:** o check "alertas do OpenObserve" do healthchecks.io precisa receber um ping depois da publicação. O `gs-heartbeat` avalia na virada de cada hora; se a virada cair com o OpenObserve parado, ele pinga logo depois da partida. Sem ping até um minuto depois da próxima virada, a publicação não acabou: veja `sudo docker logs goblinshift-openobserve-1 2>&1 | grep -E 'SCHEDULER|ERROR' | tail`.
- **Dados:** no OpenObserve, pelo túnel, um `SELECT count(*)` em `default` dos dias anteriores devolve linhas.

Só com tudo isso certo a publicação acabou. Guarde o backup até a próxima semana sem incidente e apague: `sudo shred -u "$(sudo awk '{print $2}' /var/backups/goblinshift-o2-backup.sha256)"` e `sudo rm /var/backups/goblinshift-o2-*`. Depois, religue o auto-deploy só se ele estava ligado antes do passo 1.

### Rollback

**Rollback exige restaurar o backup.** Trocar só a imagem não funciona: as 36 migrações não têm volta, e a v1.0.4 num volume migrado entra em laço de reinício, com `DB_SCHEMA_VERSION mismatch : expected 77, found 97` seguido de `Migration file of version '...' is missing`. Esse laço não estraga nada: subir a v1.1.2 de novo no mesmo volume funciona, com os dados intactos. Prefira isso a restaurar, se o problema não for da versão nova.

Restaurar perde a telemetria e as mudanças de alerta posteriores ao backup, e devolve as vulnerabilidades da v1.0.4. É solução de horas, não destino.

Numa sessão nova, repita as definições da [primeira etapa](#primeira-etapa-publicar-os-limites). O resto vem dos arquivos gravados nos passos 1 e 5. A função para antes de `docker stop`, `mv` e `tar` se o commit gravado não for fixo, se o valor for `HEAD` ou se o `sha256sum -c` falhar. Neste último caso o backup não serve, e a saída é consertar para a frente com a v1.1.2.

```bash
rollback_o2() {
  local soma backup anterior telemetria
  soma=/var/backups/goblinshift-o2-backup.sha256
  telemetria=/srv/goblinshift/telemetry
  anterior=$(sudo cat /var/backups/goblinshift-o2-commit-anterior.txt) || return 1
  [[ "$anterior" =~ ^[0-9a-f]{7,40}$ ]] || { echo "commit fixo exigido, achei: $anterior"; return 1; }
  backup=$(sudo awk '{print $2}' "$soma") || return 1
  [ -n "$backup" ] || return 1
  sudo sha256sum -c "$soma" || return 1

  sudo docker stop -t 60 goblinshift-openobserve-1 || return 1
  # Fica para diagnóstico; apague depois se faltar espaço.
  sudo mv "$telemetria/data" "$telemetria/data.v1.1.2-$(date -u +%Y%m%dT%H%M%SZ)" || return 1
  sudo tar -C "$telemetria" --numeric-owner -xzpSf "$backup" || return 1
  sudo ls -ld "$telemetria/data" "$telemetria/data/db" || return 1

  curl -fsS -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"git_commit_sha\":\"$anterior\"}" "$API" || return 1
  publicar
}
rollback_o2
```

Confira como acima, com a imagem antiga: `publicar` termina em `finished`, o log diz `DB_SCHEMA_VERSION match, skipping db upgrade`, e o `provision` mostra as mesmas 16 linhas. O Coolify recria os três serviços de novo, com queda do banco: rode antes a conferência de imagens do início da seção, porque o rollback publica como qualquer outra publicação. Depois reverta o PR na `main` para o repositório voltar a descrever a produção. A restauração precisa do `$OLD` na VPS (passo 2).

### Ensaio local (2026-11-05)

Compose derivado do `deploy/coolify/infra.json`, com os mesmos tetos de memória, CPU e processos, Postgres e Collector reais, em volumes nomeados no lugar dos binds, em arm64. A imagem amd64, a da VPS, repetiu a migração sob emulação. O que o ensaio não cobre: o Coolify, os rótulos que ele acrescenta, e o tamanho real do banco de produção.

| Passo | Resultado |
|---|---|
| v1.0.4: `provision`, 23 mil spans (20 mil depois de uma reinicialização e 3 mil ainda no WAL) e notificações chegando | 16 alertas, 3 destinos, 1 template |
| Publicar direto do `ff88bebb`, sem os limites | Plano recria Postgres, Collector e OpenObserve |
| Publicar a `main` com a v1.0.4 (primeira etapa) | Recria os três e aplica os limites; hash novo nos três |
| Trocar para a v1.1.2 no mesmo volume, já com os limites | Saudável em 3,3 s; 36 migrações em 0,13 s; WAL reaplicado; hash do Postgres e do Collector sem mudança no Compose; o Coolify os recria mesmo assim |
| Comparar antes e depois | 16 alertas iguais, só com os 3 campos novos; template e destinos iguais; os 23 mil spans, sem perda nem duplicata |
| Agendador depois da troca | Alerta de teste a cada minuto seguiu notificando; `gs-heartbeat` notificou às 11:00:30 no horário local do ensaio; o Collector seguiu gravando métricas |
| `provision` de novo na v1.1.2 | Tudo `atualizado`, nada criado ou removido; nada mudou |
| Voltar para a v1.0.4 no mesmo volume | Falha: laço de reinício com 36 migrações desconhecidas |
| v1.1.2 de novo depois dessa falha | Estado idêntico ao de antes da tentativa |
| Restaurar o backup e subir a v1.0.4 | Estado idêntico ao anterior à troca; os 2 mil spans gravados depois do backup se perderam, como esperado |
| Mesma migração na imagem amd64, sob emulação, a partir do mesmo backup | 36 migrações; 16 alertas e os 23 mil spans preservados, com os mesmos 3 campos novos |
| Carga: cerca de 340 mil spans e consultas agregadas, sob o teto de 512 MiB | Pico de 420 MiB na v1.0.4 e 438 MiB na v1.1.2; até 31 e 27 processos (teto 128); sem OOM e sem consulta com erro |

**Limite de ingestão, nas duas versões.** A memtable tem teto de 64 MiB e só gira pelo prazo de 600 s, conferido por um laço que também dorme 600 s. Uma rajada que enche o teto, cerca de 160 mil spans do ensaio em menos de um minuto, faz o OpenObserve responder 503 a tudo, métricas do Collector inclusive, por até 20 minutos: medido de 13:31 a 13:51 UTC na v1.1.2, e a v1.0.4 tem o mesmo código. Não é regressão, e o tráfego real fica longe disso, mas 503 repetido no log do Collector depois de um pico é isso, e passa sozinho.

## Testes

```bash
uv run pytest tests/openobserve -m "not openobserve"   # catálogo, segredos e relatório, sem Docker
uv run pytest -m openobserve                           # OpenObserve e Collector reais (Testcontainers)
```

Os testes com Docker (também na CI, no job paralelo "SLOs e alertas (OpenObserve)", fora do orçamento do commit stage; o cenário do agendador leva cerca de 2 min) sobem a mesma imagem do OpenObserve e do Collector do `compose.yaml`, com a configuração real do Collector, e provam: o SLI de cada consulta com valores calculados à mão; os limites da queima (exatamente 4x dispara, logo abaixo não; mínimo de eventos em cada janela; queima que já parou); a fila cheia sustentada; o disco acima de 85% só no ponto de montagem cheio; as causas lidas do span da manutenção (execução presa, limitador de exclusões, exclusão atrasada, capacidade global acima de 85% o período todo, storage sem sentinela, limitador de ausentes, objetos marcados `missing`, objetos ilegíveis, arquivos não removidos, varredura e ciclos falhando em três voltas); os contadores de eventos em qualquer span `main`; o executor que não inicia em três tentativas; a ausência de voltas e de traces, inclusive num período sem nenhum dado; e, com o agendador de verdade, que a queima, a causa e a ausência notificam e que o tráfego normal só produz o heartbeat. Só nos testes o OpenObserve roda com `ZO_SKIP_SSRF_CHECKS`, porque o receptor de webhooks está num IP privado.
