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
| `gs-heartbeat` | `heartbeat.sql`, sempre verdadeiro (stream de métricas) | 60 min / a cada 60 min | `gs-heartbeat` |

`<slo>` é `aceitacao` (meta 99,5%), `execucao` (99%) ou `download` (99,5%). Os critérios de elegível e bom estão comentados em cada `<slo>.eventos.sql`.

**Queima em duas janelas.** O alerta dispara quando a última hora **e** os últimos 15 minutos queimam o orçamento a 2x (aviso) ou 4x (urgente) o ritmo permitido. Com meta `1 - 1/N`, isso é `ruins * N >= taxa * elegíveis` em cada janela, em inteiros. Cada janela só conta com um mínimo de elegíveis (aceitação e download: 101 na hora e 26 nos 15 min; execução: 51 e 13), escolhido para que **uma falha isolada nunca dispare** (`tests/openobserve/test_catalog.py` confere isso para cada SLO e nível). Com pouco tráfego, a queima simplesmente não dispara: é o comportamento pedido em 3.7.

**Silêncio.** Depois de notificar, o urgente fica quieto por 60 min e o aviso por 180 min; a fila cheia por 60 min e o disco por 240 min. Das causas da manutenção: execução presa e exclusão atrasada, 60 min; limitador de exclusões e as três da varredura de ausentes, 180 min; capacidade global, 240 min.

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

As sete causas da manutenção leem o span `maintenance.cycle` (`goblinshift.entrypoints.maintenance.run_cycles`), emitido a cada volta (30 s) com as contagens dos dois ciclos, o de execuções e arquivos e o de contas. Contrato, conferido por `tests/runs/test_maintenance_cycle_event.py` e `tests/runs/test_maintenance_history.py`:

| Atributo | Valor |
|---|---|
| nome do span | `maintenance.cycle`, do começo ao fim da volta |
| `main` | `true` |
| `maintenance.failed_cycles` | ciclos que falharam na volta (o span fica com status de erro; a mensagem só no log) |
| `maintenance.runs_stuck_stopping` | Runs em `stopping` desde antes de 10 min (o primeiro entre o cancelamento e o prazo) |
| `maintenance.purge_limit_reached` | `1` se o limitador cortou o expurgo da volta, `0` se não |
| `maintenance.accounts_deletion_overdue` | contas com `DELETE /me` pedido há 20 h ou mais |
| `maintenance.storage_global_reserved_bytes`, `maintenance.storage_global_max_bytes` | o pool global do livro-razão (ADR 024) |
| `maintenance.runs_to_anonymize`, `maintenance.outputs_to_anonymize`, `maintenance.runs_to_delete`, `maintenance.idempotency_records_expired`, `maintenance.uploads_to_anonymize`, `maintenance.uploads_to_delete` | o que a retenção de 1.7 ainda deve: tamanho do acúmulo depois da volta |
| `maintenance.objects_missing_checked`, `maintenance.objects_missing_absent`, `maintenance.objects_missing_unreadable`, `maintenance.objects_marked_missing` | a varredura de objetos ausentes da volta: conferidos no disco, ausentes, ilegíveis (`stat` com erro que não é ausência; o objeto fica como está) e marcados `missing` (causa `gs-causa-objetos-ausentes`) |
| `maintenance.missing_sweep_skipped` | `1` se faltou a sentinela de montagem e nada foi conferido (causa `gs-causa-storage-sem-sentinela`) |
| `maintenance.missing_governor_tripped` | `1` se o limitador travou o lote e nada foi marcado: mais da metade ausente, um lote pequeno (abaixo de 10) inteiro ausente a partir de dois, ou ilegível junto com ausente (causa `gs-causa-limitador-de-ausentes`) |
| `maintenance.missing_sweep_failed` | `1` se a varredura falhou na volta (log `varredura de ausentes falhou` com o traceback); o resto do ciclo segue |
| `maintenance.<etapa>` | o que cada etapa fez na volta (`runs_deleted`, `runs_anonymized`, `file_rows_deleted`, `objects_purged`, `leases_reconciled`, …) |
| `duration_ms` | duração da volta |

Sem PII: só contagens e bytes.

### Roteiro das causas da manutenção

- **Execução presa em `stopping`.** O executor não confirmou a parada. Na VPS: `sudo systemctl status goblinshift-executor-ctl` e o log da manutenção (`executor sem inspeção` ou `executor não confirmou a parada`, com o `run_id` e o `attempt_id`). Com o `executor-ctl` de pé, `docker ps --filter name=gs-` mostra o container; a manutenção para e remove sozinha quando o Runner responde. Não apague linhas à mão: a Run termina pela reconciliação.
- **Limitador de exclusões.** Mais de 500 objetos ou 2 GiB pedidos para exclusão de uma vez. Legítimo depois de um `DELETE /me` grande ou de um dia de muito tráfego vencendo junto: as voltas seguintes terminam o serviço. Suspeito se não houver causa: pare a manutenção (`sudo docker stop goblinshift-maintenance-1`), investigue antes de deixar apagar e religue com `sudo docker start goblinshift-maintenance-1`.
- **Exclusão de conta atrasada.** A purga não termina em 20 h, quase sempre porque a conta tem Run não terminal (a purga espera) ou um ciclo de contas falhando (log `ciclo de manutenção falhou` com `cycle = contas`). Resolva antes das 24 h prometidas no aviso de privacidade.
- **Storage sem a sentinela** (`gs-causa-storage-sem-sentinela`). Falta `.goblinshift-storage` na raiz dos objetos, quase sempre o filesystem `/srv/goblinshift/storage` desmontado (`mountpoint /srv/goblinshift/storage`; o fstab usa `nofail`). Nada é marcado `missing`, mas a aplicação grava no disco do sistema. Monte o filesystem e reimplante. Só depois de conferir a montagem, a sentinela também pode ser gravada à mão com `deploy/storage-mark.sh` (leia o aviso de perigo no cabeçalho do script: não rode da cópia em `/srv/goblinshift/app`), que recusa sem montagem. Logo depois do primeiro deploy que trouxe a sentinela, o aviso para sozinho.
- **Limitador de ausentes** (`gs-causa-limitador-de-ausentes`). A varredura achou ausentes demais num lote e não marcou nada: o log `limitador de ausentes acionado` traz conferidos, ausentes e ilegíveis. Com ilegíveis, o disco devolve erro: veja `dmesg` e o filesystem antes de qualquer coisa. Sem ilegíveis, confira se o storage montado é o certo. Se é, e o banco foi restaurado de propósito de um backup mais antigo que os arquivos (3.8), desligue o limitador até a varredura dar a volta: defina `GS_MISSING_SWEEP_MAX_ABSENT_RATIO=1` como variável no recurso da aplicação no Coolify (o `deploy/coolify/app.json` a repassa à manutenção) e publique; erro de disco junto com ausente continua travando. Depois remova a variável e publique de novo para voltar ao normal.
- **Objetos marcados missing** (`gs-causa-objetos-ausentes`). Arquivos sumiram do storage montado, abaixo do limitador. Esperado depois de restaurar o banco; fora disso, investigue o que apagou arquivos fora do expurgo. `missing` é final, e a reserva do objeto já foi liberada.
- **Capacidade global perto do teto.** As reservas passam de 85% dos 20 GiB do ambiente. Veja as contas que mais ocupam (`gs-admin usage --user <id>`; `gs-admin set-limits` ajusta o teto de uma conta) e o disco do storage; se o uso é legítimo, suba o teto do pool global com cuidado com o disco real.

### Span `attempt`

O worker emite um span `attempt` por tentativa (`goblinshift.runs.worker.attempt_event`), do início do coordenador até o desfecho, inclusive quando perde o fencing. É raiz do próprio trace e tem um *link* (não é filho) para o span da requisição de aceitação, a partir do `traceparent` gravado na Run; sem `traceparent` válido, fica sem link. Hoje nenhum alerta o lê: é o evento para investigar uma tentativa. Contrato, conferido por `tests/runs/test_attempt_span.py`:

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

A publicação faz `docker compose up -d`, que recria cada serviço cujo hash de configuração mudou. Por isso esta troca tem duas etapas. O recurso foi publicado pela última vez no commit `ff88bebb`, e depois dele o `infra.json` ganhou os tetos de memória, CPU e processos dos três serviços e os buffers do OpenObserve ([ADR 013](../../docs/adr/013-openobserve-telemetria.md#teto-de-memória-2026-10-28)). Esses acréscimos mudam o hash dos três, então publicar a v1.1.2 direto daquele commit recriaria também o Postgres.

1. **Primeira etapa, à parte:** publicar a `main` ainda com a v1.0.4, só para aplicar os limites. Ela recria os três serviços, com a queda do banco que isso traz, e não migra nada.
2. **Segunda etapa, esta seção:** trocar a imagem. Daí em diante a única diferença é a linha `image` do OpenObserve, e só ele é recriado.

Ensaiado com o Compose derivado do `infra.json`: do `ff88bebb` para o PR, o plano recria os três; da `main` com limites para o PR, o Postgres e o Collector ficam `Running`, com o mesmo `StartedAt` e o mesmo hash. Os rótulos que o próprio Coolify acrescenta não entraram no ensaio, por isso o passo 4 anota o estado dos dois e a conferência o compara.

**Nunca use Stop nem Restart do recurso no painel:** eles agem sobre o recurso inteiro, inclusive o Postgres. Pare só o container do OpenObserve, como abaixo.

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

Tudo na VPS. O UUID do recurso aparece na URL dele no painel.

```bash
NEW='openobserve/openobserve:v1.1.2@sha256:4a154849432f75c4fb024de32fe34163c02406c905dab22bf8d213d2e3386392'
OLD='openobserve/openobserve:v1.0.4@sha256:d4a878fac1f6c56003764f7f2a1625668917388f167e222c8c810de3f54c56ba'
UUID=<uuid do recurso "GoblinShift - Banco e observabilidade">
TOKEN=$(sudo cat /etc/coolify-release/api-token)
API=http://127.0.0.1:8000/api/v1/applications/$UUID
```

**Pré-requisito: a primeira etapa já publicada.** Sem ela, a publicação abaixo recria o Postgres. Confira nos containers em execução, antes do merge:

```bash
sudo docker inspect -f '{{.Name}} {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}} {{.HostConfig.PidsLimit}}' \
  goblinshift-postgres-1 goblinshift-otel-collector-1 goblinshift-openobserve-1
# Só as variáveis de memória: o ambiente completo do container tem a senha do root.
sudo docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' goblinshift-openobserve-1 \
  | grep -E '^ZO_(MEMORY_CACHE_MAX_SIZE|MEMORY_CACHE_DATAFUSION_MAX_SIZE|MEM_TABLE_MAX_SIZE)='
sudo docker inspect -f '{{.Name}} {{.State.StartedAt}} {{index .Config.Labels "com.docker.compose.config-hash"}}' \
  goblinshift-postgres-1 goblinshift-otel-collector-1 goblinshift-openobserve-1
```

| Container | Memória | `NanoCpus` | Processos |
|---|---|---|---|
| `goblinshift-postgres-1` | 536870912 | 1000000000 | 256 |
| `goblinshift-otel-collector-1` | 268435456 | 250000000 | 64 |
| `goblinshift-openobserve-1` | 536870912 | 500000000 | 128 |

As variáveis precisam sair como `ZO_MEMORY_CACHE_MAX_SIZE=64`, `ZO_MEMORY_CACHE_DATAFUSION_MAX_SIZE=128` e `ZO_MEM_TABLE_MAX_SIZE=64`. Sem limite, o `docker inspect` mostra `0 0 <no value>`. O hash dos três precisa ser diferente do anotado antes da primeira etapa, e o `StartedAt` precisa ser posterior a ela. O hash inclui os rótulos do Coolify, então não dá para compará-lo com um calculado do repositório. Se algo não bater, pare: faça a primeira etapa antes do merge deste PR.

1. **Auto-deploy desligado.** Com ele ligado, o merge publica sem backup. Rode a leitura abaixo e confira `is_auto_deploy_enabled` em `settings`. Se estiver `true`, desligue antes do merge, com o mesmo `PATCH` do [ADR 041](../../docs/adr/041-ip-do-cliente-atras-da-cloudflare.md#antes-do-merge). Se não der para saber, não faça o merge. A ordem importa: sem a imagem na VPS, uma publicação falha sem tocar no container atual (ensaiado com o Compose local), mas o passo 2 tira essa proteção.

   Guarde em arquivo o `git_commit_sha`: é o destino do rollback, e precisa sobreviver a uma sessão nova. Se o valor for `HEAD`, grave no lugar dele o commit publicado na primeira etapa.

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

4. **Anote o estado e renove o backup do banco.** Com o pré-requisito, o Postgres e o Collector não reiniciam. Se o Coolify recriar o Postgres por causa de um rótulo, a aplicação cai por instantes, e é melhor ter um backup de agora.

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

6. **Publique.** Se o `git_commit_sha` do passo 1 for um commit fixo e não `HEAD`, aponte antes para o commit de merge: `curl -fsS -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"git_commit_sha":"<commit de merge>"}' "$API"`.

   ```bash
   deployment=$(curl -fsS -X POST -H "Authorization: Bearer $TOKEN" "$API/start" | jq -r .deployment_uuid)
   while :; do
     status=$(curl -fsS -H "Authorization: Bearer $TOKEN" \
       "http://127.0.0.1:8000/api/v1/deployments/$deployment" | jq -r .status)
     echo "$status"
     case "$status" in queued|in_progress) sleep 5 ;; *) break ;; esac
   done    # precisa terminar em finished
   ```

### Conferência

```bash
sudo docker inspect -f '{{.Config.Image}} {{.State.Status}} reinícios={{.RestartCount}}' goblinshift-openobserve-1
sudo docker inspect -f '{{.Name}} {{.State.StartedAt}} {{index .Config.Labels "com.docker.compose.config-hash"}}' \
  goblinshift-postgres-1 goblinshift-otel-collector-1 | diff - ~/o2-antes.txt && echo 'sem reinício e mesmo hash'
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

Só com tudo isso certo a publicação acabou. Guarde o backup até a próxima semana sem incidente e apague: `sudo shred -u "$(sudo awk '{print $2}' /var/backups/goblinshift-o2-backup.sha256)"` e `sudo rm /var/backups/goblinshift-o2-backup.sha256 /var/backups/goblinshift-o2-commit-anterior.txt`. Depois, religue o auto-deploy só se ele estava ligado antes do passo 1.

### Rollback

**Rollback exige restaurar o backup.** Trocar só a imagem não funciona: as 36 migrações não têm volta, e a v1.0.4 num volume migrado entra em laço de reinício, com `DB_SCHEMA_VERSION mismatch : expected 77, found 97` seguido de `Migration file of version '...' is missing`. Esse laço não estraga nada: subir a v1.1.2 de novo no mesmo volume funciona, com os dados intactos. Prefira isso a restaurar, se o problema não for da versão nova.

Restaurar perde a telemetria e as mudanças de alerta posteriores ao backup, e devolve as vulnerabilidades da v1.0.4. É solução de horas, não destino.

Numa sessão nova, defina `TOKEN` e `API` como em [Antes do merge](#antes-do-merge). O resto vem dos arquivos gravados nos passos 1 e 5. Se o `sha256sum -c` falhar, pare: o backup não serve, e a saída é consertar para a frente com a v1.1.2.

```bash
soma=/var/backups/goblinshift-o2-backup.sha256
backup=$(sudo awk '{print $2}' "$soma")
anterior=$(sudo cat /var/backups/goblinshift-o2-commit-anterior.txt)
sudo sha256sum -c "$soma"

sudo docker stop -t 60 goblinshift-openobserve-1
cd /srv/goblinshift/telemetry
sudo mv data "data.v1.1.2-$(date -u +%Y%m%dT%H%M%SZ)"    # para diagnóstico; apague se faltar espaço
sudo tar -C /srv/goblinshift/telemetry --numeric-owner -xzpSf "$backup"
sudo ls -ld data data/db

curl -fsS -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"git_commit_sha\":\"$anterior\"}" "$API"
deployment=$(curl -fsS -X POST -H "Authorization: Bearer $TOKEN" "$API/start" | jq -r .deployment_uuid)
```

Espere o deployment como no passo 6 e confira como acima, com a imagem antiga: o log diz `DB_SCHEMA_VERSION match, skipping db upgrade`, e o `provision` mostra as mesmas 16 linhas. O commit anotado já tem os limites, então aqui também só o OpenObserve é recriado. Depois reverta o PR na `main` para o repositório voltar a descrever a produção. A restauração precisa do `$OLD` na VPS (passo 2).

### Ensaio local (2026-11-05)

Compose derivado do `deploy/coolify/infra.json`, com os mesmos tetos de memória, CPU e processos, Postgres e Collector reais, em volumes nomeados no lugar dos binds, em arm64. A imagem amd64, a da VPS, repetiu a migração sob emulação. O que o ensaio não cobre: o Coolify, os rótulos que ele acrescenta, e o tamanho real do banco de produção.

| Passo | Resultado |
|---|---|
| v1.0.4: `provision`, 23 mil spans (20 mil depois de uma reinicialização e 3 mil ainda no WAL) e notificações chegando | 16 alertas, 3 destinos, 1 template |
| Publicar direto do `ff88bebb`, sem os limites | Plano recria Postgres, Collector e OpenObserve |
| Publicar a `main` com a v1.0.4 (primeira etapa) | Recria os três e aplica os limites; hash novo nos três |
| Trocar para a v1.1.2 no mesmo volume, já com os limites | Saudável em 3,3 s; 36 migrações em 0,13 s; WAL reaplicado; Postgres e Collector sem reinício |
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

Os testes com Docker (também na CI, no job paralelo "SLOs e alertas (OpenObserve)", fora do orçamento do commit stage; o cenário do agendador leva cerca de 2 min) sobem a mesma imagem do OpenObserve e do Collector do `compose.yaml`, com a configuração real do Collector, e provam: o SLI de cada consulta com valores calculados à mão; os limites da queima (exatamente 4x dispara, logo abaixo não; mínimo de eventos em cada janela; queima que já parou); a fila cheia sustentada; o disco acima de 85% só no ponto de montagem cheio; as sete causas lidas do span da manutenção (execução presa, limitador de exclusões, exclusão atrasada, capacidade global acima de 85% o período todo, storage sem sentinela, limitador de ausentes e objetos marcados `missing`); e, com o agendador de verdade, que a queima e a causa notificam sob erro e que o tráfego normal só produz o heartbeat. Só nos testes o OpenObserve roda com `ZO_SKIP_SSRF_CHECKS`, porque o receptor de webhooks está num IP privado.
