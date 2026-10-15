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
| `gs-heartbeat` | `heartbeat.sql`, sempre verdadeiro (stream de métricas) | 60 min / a cada 60 min | `gs-heartbeat` |

`<slo>` é `aceitacao` (meta 99,5%), `execucao` (99%) ou `download` (99,5%). Os critérios de elegível e bom estão comentados em cada `<slo>.eventos.sql`.

**Queima em duas janelas.** O alerta dispara quando a última hora **e** os últimos 15 minutos queimam o orçamento a 2x (aviso) ou 4x (urgente) o ritmo permitido. Com meta `1 - 1/N`, isso é `ruins * N >= taxa * elegíveis` em cada janela, em inteiros. Cada janela só conta com um mínimo de elegíveis (aceitação e download: 101 na hora e 26 nos 15 min; execução: 51 e 13), escolhido para que **uma falha isolada nunca dispare** (`tests/openobserve/test_catalog.py` confere isso para cada SLO e nível). Com pouco tráfego, a queima simplesmente não dispara: é o comportamento pedido em 3.7.

**Silêncio.** Depois de notificar, o urgente fica quieto por 60 min e o aviso por 180 min; a fila cheia por 60 min e o disco por 240 min.

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

### Fora do OpenObserve, por enquanto

| Causa de 3.7 | Onde fica |
|---|---|
| Backup diário que falhou | healthchecks.io (ping do `pg-backup.sh`) |
| Certificado TLS a menos de 14 dias | monitor de uptime externo (UptimeRobot ou Better Stack) |
| Teste de aceitação do ambiente falhando | healthchecks.io (ping do `deploy/acceptance/scheduled.sh`, pelo timer semanal) |
| Execução presa em `stopping` por mais de 10 min | falta o evento por ciclo da manutenção |
| Limitador de exclusões acionado | falta o evento por ciclo da manutenção |

### Monitores externos

O OpenObserve roda na própria VPS: se ela cair, ninguém avisa. Por isso estes monitores ficam fora dela (plano da fatia 8, 2.2 e 2.3). O healthchecks.io é passivo (recebe pings e avisa quando eles não chegam); o monitor de uptime é ativo (sonda a VPS de fora). As URLs de ping são segredo, porque quem as tem cala o alarme com pings falsos de sucesso: nenhuma fica no repositório.

| Monitor | Serviço | Período / tolerância | Quem pinga ou o que sonda | Onde fica a URL |
|---|---|---|---|---|
| `backup diário` | healthchecks.io | 1 dia / 2 h | `deploy/backup/pg-backup.sh` (`/start`, sucesso, `/fail`) | `BACKUP_HEALTHCHECK_URL` em `/srv/goblinshift/secrets/backup.env` |
| `restauração mensal` | healthchecks.io | 31 dias / 1 dia | workflow "Restauração testada do backup" | secret `RESTORE_HEALTHCHECK_URL` (environment `backup-restore`) |
| `alertas do OpenObserve` | healthchecks.io | 1 h / 30 min | alerta `gs-heartbeat` (prova que o OpenObserve avalia e alcança a internet) | `GS_ALERT_HEARTBEAT_URL` em `~/.config/goblinshift/alertas.env` |
| `aceitação semanal` | healthchecks.io | 7 dias / 2 h | `deploy/acceptance/scheduled.sh` pelo `goblinshift-acceptance.timer` (`/start`, sucesso, `/fail`) | `ACCEPTANCE_HEALTHCHECK_URL` em `/etc/goblinshift/acceptance.env` (root, `chmod 600`) |
| `/readyz` e TLS | UptimeRobot (ou Better Stack) | 5 min; alerta depois de 2 falhas | `GET /readyz` esperando 200, certificado a menos de 14 dias e expiração do domínio | na conta do serviço; não há URL de ping |

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

Também dá para rodar na própria VPS, sem túnel, como o usuário `goblinshift` (dono de `/srv/goblinshift/secrets`, modo 700), somando o `app.env` (credencial) a um arquivo só com as URLs, também com `chmod 600`:

```bash
cd /srv/goblinshift/app/deploy
sudo -u goblinshift python3 -B -m openobserve provision \
  --secrets-file /srv/goblinshift/secrets/app.env --secrets-file /srv/goblinshift/secrets/alertas.env
```

**Disco e heartbeat.** O alerta de disco e o heartbeat leem `system_filesystem_utilization`, que o Collector produz com as montagens do `compose.prod.yaml`. O provisionamento falha enquanto esse stream não existir: confira que o deploy com o Collector novo já rodou. O `release.sh` só recria o Collector quando o serviço muda no Compose; depois de mudar só o `docker/otel-collector/config.yaml`, rode `sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh restart otel-collector` na VPS.

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
# Todo comando do Compose de produção passa pelo deploy/compose.sh (README, "Contas e chaves de API").
compose=(sudo -u goblinshift /srv/goblinshift/app/deploy/compose.sh)
"${compose[@]}" stop postgres
# Chave no formato válido, mas inexistente: nada é criado.
key="gsk_0000000000000000_$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=')"
for i in $(seq 130); do
  curl -s -o /dev/null -w '%{http_code}\n' -X POST <endereço público>/api/v1/runs \
    -H "Authorization: Bearer $key" -H "Idempotency-Key: drill-$(printf '%016d' "$i")" \
    -H 'Content-Type: application/json' -d '{}'
done | sort | uniq -c        # esperado: 130 respostas 500
"${compose[@]}" start postgres
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

## Testes

```bash
uv run pytest tests/openobserve -m "not openobserve"   # catálogo, segredos e relatório, sem Docker
uv run pytest -m openobserve                           # OpenObserve e Collector reais (Testcontainers)
```

Os testes com Docker (também na CI, no job paralelo "SLOs e alertas (OpenObserve)", fora do orçamento do commit stage; o cenário do agendador leva cerca de 2 min) sobem a mesma imagem do OpenObserve e do Collector do `compose.yaml`, com a configuração real do Collector, e provam: o SLI de cada consulta com valores calculados à mão; os limites da queima (exatamente 4x dispara, logo abaixo não; mínimo de eventos em cada janela; queima que já parou); a fila cheia sustentada; o disco acima de 85% só no ponto de montagem cheio; e, com o agendador de verdade, que a queima e a causa notificam sob erro e que o tráfego normal só produz o heartbeat. Só nos testes o OpenObserve roda com `ZO_SKIP_SSRF_CHECKS`, porque o receptor de webhooks está num IP privado.
