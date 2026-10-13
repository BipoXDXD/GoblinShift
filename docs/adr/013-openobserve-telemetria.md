# ADR 013. OpenObserve auto-hospedado como backend de telemetria

- **Estado:** aceito (confirmado pela medição da fatia 0, 2026-10-13)
- **Plano:** [3.7](../GoblinShiftPlan.md#s3-7), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [D29](../GoblinShiftReferencias.md#d-6)

## Contexto

A aplicação emite traces e logs por OTLP para um OpenTelemetry Collector local. Os SLOs são baseados em eventos, numa janela deslizante de 30 dias, e os alertas de queima de orçamento precisam de consultas sobre esses eventos. O backend tem de caber na VPS KVM 2, que é compartilhada com outro projeto, sem custo mensal e sem tirar os dados da VPS.

## Decisão

Um OpenObserve em nó único (`ZO_LOCAL_MODE`, SQLite e disco local), no filesystem próprio `/srv/goblinshift/telemetry` (10 GiB), com retenção explícita de 30 dias (`ZO_COMPACT_DATA_RETENTION_DAYS`). Os SLOs e alertas são alertas agendados com consulta SQL sobre o stream de traces. A interface fica presa a `127.0.0.1` e é acessada por túnel SSH.

## Alternativas descartadas

| Alternativa            | Motivo                                                       |
|------------------------|--------------------------------------------------------------|
| Honeycomb gratuito     | Dados fora da VPS; limites do plano gratuito                 |
| Grafana Cloud gratuito | Guarda só 14 dias, menos que a janela de 30 dias dos SLOs    |
| SigNoz auto-hospedado  | Pede de 4 a 8 GB de RAM, mais do que a VPS compartilhada tem |

## Medição na VPS (fatia 0)

Medido em 2026-10-13 com o OpenObserve v1.0.4, na própria VPS, com 20 000 requisições sintéticas (`GET /api/v1/sintetico/{n}`, 20 em paralelo, pelo proxy de borda), cerca de 160 req/s, que geraram cerca de 60 000 spans.

| Medida                                 | Resultado                                                                                      |
|----------------------------------------|------------------------------------------------------------------------------------------------|
| RAM da pilha em repouso (6 containers) | cerca de 385 MiB                                                                               |
| RAM do OpenObserve (repouso → pico)    | 210 → 246 MiB                                                                                  |
| RAM do Collector (repouso → pico)      | 35 → 56 MiB                                                                                    |
| CPU do OpenObserve no pico             | 2,5%                                                                                           |
| api no pico (referência)               | 98 MiB de RAM, 47% de CPU                                                                      |
| Disco                                  | 60 000 spans → 3,1 MiB de parquet + 0,6 MiB de índice (cerca de 62 bytes por span)             |
| SQL sobre traces                       | Funciona: proporção de bons sobre elegíveis e p99 por `http_status_code` nos spans de servidor |
| Alerta agendado sobre traces           | Funciona: `stream_type: traces`, consulta SQL, disparou o webhook no período configurado       |

Por isso, o plano B (emitir também os eventos `main` como registros de log OTel) não é necessário.

Com 10 GiB e retenção de 30 dias, o orçamento é de cerca de 340 MiB por dia, folga de duas ordens de grandeza para o tráfego esperado. Os spans medidos eram `404` mínimos; os reais, com o evento `main` e os spans de tentativa, são maiores, e a medida é refeita quando a fatia 3 trouxer o fluxo durável. Os arquivos `.wal` aparecem com 512 MiB cada em `du --apparent-size`, mas são pré-alocação esparsa: o uso real é o de `df` no filesystem de telemetria.

## Consequências

- Destinos de alerta precisam ser URLs públicas ou e-mail: o OpenObserve recusa destinos com IP privado (proteção contra SSRF), inclusive containers da rede interna.
- Alertas agendados não aceitam `SELECT *`; a consulta precisa nomear as colunas.
- Se a VPS cair, a telemetria cai junto. Um monitor de uptime externo consulta `/readyz` por um canal independente ([3.7](../GoblinShiftPlan.md#s3-7)).
- Com o filesystem de telemetria cheio, o OpenObserve para de ingerir e continua respondendo; "telemetria acima de 85%" é alerta de causa.
- Como a aplicação só fala OTLP com o Collector, trocar de backend não muda o código.

## Como verificar

- `compose.yaml` mantém `ZO_LOCAL_MODE`, `ZO_COMPACT_DATA_RETENTION_DAYS: "30"` e a porta publicada só em `127.0.0.1`.
- Na VPS, `docker stats` do OpenObserve abaixo de 512 MiB em uso normal; se passar disso, este ADR é revisto.
- `df -h /srv/goblinshift/telemetry` abaixo de 85%.
