# ADR 003. Sem Redis

- **Estado:** aceito
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [2.1](../GoblinShiftPlan.md#s2-1), [3.1](../GoblinShiftPlan.md#s3-1), [3.3](../GoblinShiftPlan.md#s3-3), [3.4](../GoblinShiftPlan.md#s3-4), [3.6](../GoblinShiftPlan.md#s3-6), [3.8](../GoblinShiftPlan.md#s3-8), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** nenhuma; o plano não cita fonte para esta decisão

## Contexto

Os papéis em que o Redis entraria são quatro: fila, cache, sessões e rate limiting. O serviço roda numa VPS compartilhada com outro projeto e processa uma tentativa física por vez, com no máximo 10 execuções aguardando globalmente. A pergunta é se algum desses papéis compensa um armazenamento a mais.

## Decisão

Não há Redis, e a arquitetura exclui também outro broker de mensagens ([3.1](../GoblinShiftPlan.md#s3-1)). Cada papel é coberto assim:

- **Fila:** a tabela `Run` no PostgreSQL (ADR 002).
- **Cache:** o estado transitório do login (`state`, `nonce`, `code_verifier`) fica no PostgreSQL, por `OAuth(cache=…)` do Authlib, com expiração de poucos minutos. O cache de dados do cliente é o do TanStack Query, em memória no navegador.
- **Sessões:** `WebSession` no PostgreSQL, com cookie opaco e hash do segredo no banco.
- **Rate limiting:** `limit_req` no Nginx para `/auth/*`, por IP. Os limites por conta e classe de rota (leituras 120 por minuto, comandos 60, criação de execução 10) estão definidos em [1.7](../GoblinShiftPlan.md#s1-7); as cotas diárias são calculadas a partir de `Run` e `RunInput` das últimas 24 horas, sem tabela de contadores.

## Alternativas descartadas

| Alternativa                                      | Motivo                                                                                    |
|--------------------------------------------------|-------------------------------------------------------------------------------------------|
| Redis para fila, cache, sessões ou rate limiting | Nenhum desses papéis compensa um armazenamento a mais com uma VPS e uma tentativa por vez |

## Consequências

- A sessão vive no banco, então `last_seen_at` é atualizado no máximo uma vez por minuto, para não gravar no banco a cada requisição.
- Revogação de sessão e de chave acontece no servidor, no mesmo banco que guarda os dados de autorização.
- Estado transitório de login e sessões expiradas são removidos pelo processo `maintenance` (sessões expiradas ou revogadas, 7 dias após o fim).
- O plano não define o mecanismo dos limites por conta e classe de rota, só os valores e o Nginx para `/auth/*`; este ADR fixa apenas que o mecanismo não será Redis.

## Como verificar

- `grep -ri redis pyproject.toml uv.lock compose*.yaml` não encontra nada, e a lista de serviços do Compose ([3.8](../GoblinShiftPlan.md#s3-8)) não inclui Redis.
- Na fatia 6, o estado de login do Authlib e as sessões ficam em tabelas do PostgreSQL, e a suíte de segurança de [3.7](../GoblinShiftPlan.md#s3-7) passa sem outro armazenamento.
- Na fatia 8, o teste de carga simples confirma os limites por classe de rota.
