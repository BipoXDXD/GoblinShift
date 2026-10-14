# ADR 027. Limite de requisições por conta em janela fixa no PostgreSQL

- **Estado:** aceito na fatia 8 (2026-10-14), decisão D-9 do plano da fatia 6 (tarefa F8-1)
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [3.7](../GoblinShiftPlan.md#s3-7)
- **Relacionado:** [ADR 003](003-sem-redis.md), [ADR 012](012-servico-aberto-com-cotas.md), [ADR 024](024-livro-razao-de-capacidade.md)

## Contexto

1.7 limita as requisições **por conta e por classe de rota**: leituras e polling a 120 por minuto, comandos a 60 e criação de execução a 10. O envio e o download de conteúdo ficam de fora, porque valem as transferências simultâneas. As rotas `/auth/*` têm o limite por IP no Nginx. A fatia 6 só classificou as rotas (`rate_limit.class` no evento amplo) e deixou o mecanismo para a fatia 8 (ADR 024). O ADR 003 exclui o Redis.

O limite precisa valer entre réplicas da API, contar a conta e não o IP, e nunca deixar passar mais do que o limite quando várias requisições chegam juntas.

Alternativas consideradas:

| Alternativa | Prós | Contras |
|---|---|---|
| **Janela fixa no PostgreSQL, um `INSERT … ON CONFLICT DO UPDATE … WHERE` por requisição** | Atômico pela trava da linha, vale entre réplicas, sem serviço novo, testável com o banco real | Uma escrita por requisição limitada; rajada de até 2× na virada do minuto |
| Contador em memória do processo | Sem escrita no banco | Cada réplica tem o seu, o limite multiplica pelo número de réplicas e zera no restart |
| `limit_req` do Nginx | Já existe para `/auth/*` | Não conhece a conta, só o IP: trocar de IP escapa, e o NAT pune contas inocentes |
| Janela deslizante (log de requisições ou duas janelas ponderadas) | Sem a rajada da virada | Uma linha por requisição ou aritmética de aproximação, para um ganho que esta escala não pede |
| Redis | Contadores com TTL nativo | Excluído pelo ADR 003: mais um armazenamento numa VPS só |

## Decisão

- **Tabela `request_window`** (migração 0007), com a chave primária `(owner_id, request_class, window_start)` e a coluna `request_count`. Há um `CHECK` com as três classes de `RequestClass` e outro com `request_count > 0`. A FK para `app_user` usa `ON DELETE CASCADE`: a linha é dado efêmero, a purga da conta a leva junto, sem etapa própria.
- **Janela fixa de um minuto em UTC**, a partir do relógio da aplicação (`current_time`, injetável nos testes).
- **Contagem atômica**: `INSERT … VALUES (…, 1) ON CONFLICT (pk) DO UPDATE SET request_count = request_count + 1 WHERE request_count < :limite RETURNING request_count`. Sem linha devolvida, a requisição é recusada. A disputa pela mesma janela, inclusive pela primeira inserção, é serializada pelo próprio PostgreSQL, sem "ler e depois gravar" no código.
- **Transação própria e curta**, logo depois da autenticação: a requisição conta mesmo que falhe depois, inclusive com 4xx.
- **Ponto único na borda**: a contagem fica dentro de `authenticate`, a dependência pela qual passa toda rota autenticada. Assim nenhuma rota nova escapa do limite por esquecimento. A classe vem de `rate_limit_class(método, template)`, a mesma função da telemetria. O template completo da rota vem de `fastapi.routing.iter_route_contexts`, porque o `scope["route"]` traz só o trecho do roteador mais interno. `TRANSFER` não conta.
- **Resposta**: `429 RATE_LIMITED` em problem+json, com `Retry-After` igual ao tempo até o fim da janela, em segundos inteiros arredondados para cima, nunca 0. O evento amplo ganha `rate_limit.limited` (verdadeiro ou falso) em toda requisição autenticada que passou pelo limite.
- **Requisição sem credencial não conta**: ela não tem conta, termina no 401 e não grava nada.
- **Limpeza**: o ciclo de contas da manutenção apaga em lotes (`FOR UPDATE SKIP LOCKED`) as janelas anteriores à janela anterior. A anterior fica, porque uma réplica com o relógio um pouco atrasado ainda conta nela. O evento do ciclo registra `request_windows_removed` e o tamanho que sobrou, `request_windows`.
- **Sem índice em `window_start`**: a tabela guarda só as janelas dos últimos minutos de cada conta ativa, e a limpeza lê poucas linhas. Um índice entra só se a consulta medida pedir (regra SQL 9).

## Consequências

- Cada requisição autenticada fora das transferências custa mais uma escrita curta no banco. O teste de carga da fatia 8 (F8-2) mede esse custo contra os SLOs.
- Na virada do minuto, uma conta pode fazer até o dobro do limite em poucos segundos. O limite existe para proteger a VPS de uma conta só, não para medir vazão exata.
- Relógios de réplicas diferentes podem discordar em alguns milissegundos. Na fronteira, uma requisição pode cair na janela vizinha, e isso não muda o teto de cada janela.
- Durante o deploy da 0007, a API anterior não conta requisições. A tabela nova é ignorada por ela (*expand*).
- Requisições sem credencial ficam sem limite por conta. O 401 sai antes de qualquer trabalho caro, e o abuso por IP fica com o Nginx.

## Como verificar

- `tests/identity/test_rate_limit.py`: a requisição de número `limite` passa e a seguinte é recusada, nas três classes; o `retry_after` é o tempo exato até o fim da janela; a janela é o minuto em UTC; contas e classes são independentes; requisições simultâneas, com threads e barreira, não passam do limite, nem na fronteira nem na janela nova; a limpeza respeita a janela anterior e o lote; o banco recusa classe desconhecida e contagem não positiva.
- `tests/api/test_rate_limit.py`: a 121ª leitura, o 61º comando e a 11ª criação de execução recebem `429 RATE_LIMITED` com `Retry-After`; a conta continua limitada vinda de outro IP; outra conta no mesmo IP não é afetada; o `Retry-After` segue o relógio injetado e a conta volta a ser atendida depois dele; o envio de conteúdo e a requisição sem credencial não contam.
- `tests/api/test_wide_event.py`: `rate_limit.limited` é falso na requisição atendida, verdadeiro com `error.code = RATE_LIMITED` na recusada, e ausente sem credencial.
- `tests/runs/test_maintenance_identity.py`: o ciclo de contas remove as janelas velhas e registra o tamanho; a purga da conta não deixa janela para trás.
- `tests/test_schema_constraints.py`: a migração 0007 bate com o modelo e é reversível.
