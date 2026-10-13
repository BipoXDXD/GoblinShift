# ADR 002. PostgreSQL como fonte de verdade e como fila

- **Estado:** aceito
- **Plano:** [2.1](../GoblinShiftPlan.md#s2-1), [3.1](../GoblinShiftPlan.md#s3-1), [3.4](../GoblinShiftPlan.md#s3-4), [3.5](../GoblinShiftPlan.md#s3-5), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [D08](../GoblinShiftReferencias.md#d-2), [D09](../GoblinShiftReferencias.md#d-2), [B19](../GoblinShiftReferencias.md#l-6)

## Contexto

Aceitar uma execução e colocá-la na fila precisa ser uma operação só. Com um broker, aceitar trabalho exige gravar em dois armazenamentos, o que obriga a ter outbox, dispatcher, confirmação de publicação e consumidor idempotente. A escala do serviço é pequena: uma tentativa física por vez e no máximo 10 execuções aguardando globalmente.

## Decisão

O PostgreSQL guarda identidade, execuções, presets, idempotência e reservas, e a própria tabela `Run` é a fila. A aceitação grava a `Run` com `status=queued`, `eligible_at` e `queue_expires_at` numa transação curta, junto com a idempotência, a reserva de capacidade e o vínculo das entradas; o mesmo commit emite `NOTIFY goblinshift_runs`, e o `202` só sai depois do commit. O worker espera com `LISTEN` e consulta a cada 2 segundos como garantia contra notificações perdidas. O claim usa `FOR UPDATE SKIP LOCKED` com `LIMIT 1` e, na mesma transação, incrementa `Run.generation`, cria a `Attempt` com essa geração e passa a Run para `running`. A geração é o token de fencing: toda escrita do coordenador é condicional a ela.

## Alternativas descartadas

| Alternativa       | Motivo                                                                                                                                                                                                                              |
|-------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| RabbitMQ + Celery | Dupla escrita ao aceitar (outbox, dispatcher, confirmação, consumidor idempotente) e as armadilhas de `acks_late`, `reject_on_worker_lost` e do `consumer_timeout` de 30 minutos do RabbitMQ diante de tentativas de 2 × 10 minutos |
| Redis             | O mesmo problema de dupla escrita; ver também o ADR 003                                                                                                                                                                             |

## Consequências

- Não existe dupla escrita para reconciliar: aceitar e enfileirar, e também o claim e a criação da tentativa, são a mesma transação.
- A DDIA (cap. 12) alerta para bancos usados como fila sob alto volume, o que não é o caso na escala do serviço; a contenção é desprezível.
- Unicidade e geração impedem publicação lógica duplicada, mas não prometem que o cálculo físico ocorrerá exatamente uma vez.
- Se a escala mudar para vários workers em máquinas diferentes, a troca de broker continua possível por trás de uma interface `JobQueue` estreita.
- Falha do banco impede aceitar, iniciar e publicar trabalho, e também consultas e downloads.
- Uma execução reivindicada 4 vezes sem que nenhuma tentativa termine termina `failed` com `INFRA_POISON`, em vez de ocupar o único executor para sempre.

## Como verificar

- `pyproject.toml` e `uv.lock` não têm broker nem cliente de fila (Celery, kombu, pika, Redis), e o `compose.yaml` não tem serviço de broker.
- Na fatia 3, a migração cria a tabela `Run` com `eligible_at`, `queue_expires_at`, `generation` e `claim_count`, e um índice único parcial garante uma `Attempt` sem `ended_at` por Run.
- Na fatia 3, passam os testes de concorrência com PostgreSQL real ([3.7](../GoblinShiftPlan.md#s3-7)): dois workers disputando o mesmo claim, escrita com geração vencida afetando zero linhas, publicação contra cancelamento e contra prazo vencido.
- Na fatia 4, os testes de caos (worker morto por SIGKILL e pausado por SIGSTOP além do lease) passam na VPS sem publicação duplicada.
