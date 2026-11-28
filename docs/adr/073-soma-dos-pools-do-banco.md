# ADR 073. Um pool por processo e a soma dos pools registrada

- **Estado:** aceito (coordenação, 28/11, por delegação do dono). Fixar `max_connections` fica com o dono.
- **Plano:** [3.2](../GoblinShiftPlan.md#s3-2) (banco: pool e timeouts)
- **Relacionado:** issue #442 ("Banco: soma dos pools…", item a), [ADR 050](050-sondas-de-saude-sob-o-teto-de-concorrencia.md), `src/goblinshift/platform/db.py`, `tests/test_db_pool_budget.py`

## Contexto

Todo processo abria o engine com o pool das settings: 10 conexões fixas e 5 de overflow. Com a API em duas réplicas no rollout, o worker com o `LISTEN`, a manutenção, o migrate e o `gs-admin`, o pico passava de 90 conexões, perto do teto de `max_connections - superuser_reserved_connections` (100 − 3 no PostgreSQL 18 padrão). Nenhum ADR registrava essa conta, e um processo novo podia estourá-la sem que ninguém percebesse.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Pool menor na API** | Maior folga | O teto do middleware deriva do pool da API (ADR 050); mexer nele reabre aquele ADR |
| **B. PgBouncer na frente do Postgres** | Multiplexa conexões; folga quase ilimitada | Mais um serviço para operar; o modo transação quebra o `LISTEN` e os `SET` de sessão do `make_engine` |
| **C. Subir `max_connections`** | Nada muda no código | Recria o Postgres; mais memória por conexão; decisão do dono |
| **D. Pools pequenos e nomeados nos processos fora da API, com a soma num teste** (escolhida) | A API não muda; a conta vira código e falha no CI | Um processo novo precisa entrar na conta |

## Decisão

Opção D. `make_engine(settings, *, pool=None)` recebe um `PoolSize(size, overflow)`; com `None`, usa o pool das settings, o caso da API e dos healthchecks do `app.json`.

| Processo | Pool | Pico | Observação |
|----------|------|------|------------|
| api | settings (10 + 5) | 2 × 15 = 30 | duas réplicas durante o rollout |
| worker | `WORKER_POOL` (2 + 1) | 3 + 1 = 4 | pico medido de 1 conexão do pool; o 2 + 1 é margem. A renovação do lease em thread só roda durante o `start` e o `stop` do executor, quando o laço não segura conexão. O `LISTEN` fica numa conexão própria, fora do pool |
| maintenance | `MAINTENANCE_POOL` (2 + 1) | 3 | ciclos em sequência |
| gs-admin | `ADMIN_POOL` (1 + 0) | 1 | uma transação por vez; o cancelamento e o `run.finished` rodam depois do commit |
| migrate | `MIGRATE_POOL` (1 + 0) | 1 | uma conexão por rodada, cada migration na sua transação |
| healthchecks | settings, uma conexão cada | 2 | worker e manutenção no `app.json` |
| backup | `pg_dump` | 1 | |
| **Total** | | **42** | teto 97 (100 − 3): folga de 55 |

O teste lê `-c max_connections=` do serviço postgres em `compose.yaml` e `deploy/coolify/infra.json`; sem a opção, conta o padrão de 100.

## Consequências

- Um processo novo, ou um pool maior, entra em `PEAK_CONNECTIONS` de `tests/test_db_pool_budget.py`; se a soma passar do teto, o teste falha.
- Pool esgotado no worker ou na manutenção espera o `pool_timeout` (5 s) e falha com `TimeoutError`, como na API. No worker, o pico medido de 1 conexão deixa o 2 + 1 com margem. Se uma mudança fizer o laço e a renovação segurarem conexão juntos, o teste de pool do worker acusa.
- A folga cobre conexões manuais de operação (`psql`) e até três réplicas a mais da API (45 conexões); a quarta passaria do teto.

## Compliance

- `tests/test_db_pool_budget.py`: a soma cabe no teto; a API fica em 10 + 5; worker, manutenção e `gs-admin` pedem o próprio pool ao compor o processo; o `env.py` pede o `MIGRATE_POOL`.
- `tests/runs/test_worker_process.py`, `test_worker_completa_a_run_dentro_do_worker_pool`: roda o `serve` no processo do teste com o `WORKER_POOL`. Faz claim, renova o lease durante um `start` que bloqueia por dois leases, coleta e publica. O pico de conexões em uso, medido pelos eventos `checkout` e `checkin` do pool, é exatamente 1: o laço e a renovação nunca seguram conexão juntos.
- `tests/test_db_pool_budget.py` também recusa `make_engine(` sem `pool=` fora de `api/app.py` e `GS_DB_POOL_SIZE` ou `GS_DB_POOL_MAX_OVERFLOW` em `compose.yaml` e `deploy/coolify/*.json`.
- Os testes do `gs-admin` (`tests/identity/test_admin_cli*.py`) e das migrações rodam contra PostgreSQL com os pools de 1 conexão.
