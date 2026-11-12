# ADR 053. Varredura de ausentes e pool esgotado sinalizam perda de conexão ao ciclo

- **Estado:** aceito (2026-11-12 UTC)
- **Plano:** [3.4](../GoblinShiftPlan.md#s3-4) (manutenção) e [3.7](../GoblinShiftPlan.md#s3-7) (sinais e alertas)
- **Relacionado:** issue #333, #322 e #323 (perda de conexão pula os passos de banco restantes), [ADR 047](047-pausa-de-claims-apos-falha-de-banco.md)

## Contexto

Desde o #322, o ciclo da manutenção pula os passos de banco restantes quando um passo perde a conexão, porque cada um só esperaria o timeout de novo. Dois casos escapavam disso:

- **Pool esgotado.** O pool desiste com `sqlalchemy.exc.TimeoutError`, que não é `DBAPIError`, e `_is_connection_error` não o reconhecia. Cada passo seguinte esperava o `pool_timeout` (5 s). O worker já o trata como transitório (`TRANSIENT_DATABASE_ERRORS`).
- **Varredura de ausentes (`mark_missing_objects`).** O passo captura qualquer `Exception`, registra `varredura de ausentes falhou` e devolve `missing_sweep_failed=1`. O ciclo nunca soube que o banco caiu, e `measure` esperava o `connect_timeout`.

O segundo caso tocava o alerta `gs-causa-varredura-falhou` (3 ou mais voltas com `maintenance.missing_sweep_failed = 1`), que por contrato não entra em `maintenance.failed_cycles`. Por isso dependia de decisão.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Manter a varredura como está** | Nenhuma mudança de sinal | `measure` espera o `connect_timeout` a cada ciclo com o banco fora |
| **B. Relançar o erro sem contagens** | Passo igual aos outros | A contagem `missing_sweep_failed` some nesses ciclos, e o alerta `varredura-falhou` deixa de ver a perda de conexão |
| **C. Manter log e contagens e também sinalizar o ciclo** (escolhida) | O alerta segue como era; o ciclo passa a pular os passos de banco restantes; erro que não é de conexão não muda | Uma exceção interna a mais |

## Decisão

Opção C. Na perda de conexão (`_is_connection_error`), `mark_missing_objects` mantém o log `varredura de ausentes falhou` e as contagens com `missing_sweep_failed=1`, e levanta `_ConnectionLostWithCountsError`, uma exceção interna que carrega as contagens e o erro. `_CycleOutcome.run` aplica as contagens e trata o passo como falho: conta em `steps_failed` (e, no span, em `failed_cycles`), registra `connection_lost_in` e pula os passos de banco restantes. Erros que não são de conexão (disco, sentinela) seguem como antes. `_is_connection_error` também passa a reconhecer o `TimeoutError` do pool.

A exceção interna foi a forma mais simples: o passo continua devolvendo contagens no caminho normal, e a regra de "falha de conexão" fica só em `_CycleOutcome`, sem estado em `Maintenance`.

Por quê: a mudança é aditiva (nenhum sinal existente deixa de ser emitido) e reversível (basta deixar de levantar a exceção). O alerta `varredura-falhou` continua cobrindo a perda de conexão na varredura, e o ciclo não espera o `connect_timeout` em cada passo seguinte.

## Consequências

- Ciclo em que a varredura perde a conexão agora também conta em `steps_failed` e `failed_cycles`, e alimenta `gs-causa-ciclos-falhando` junto com `gs-causa-varredura-falhou`. Os dois limiares seguem em 3 voltas.
- `measure` é pulado nesse ciclo (conta em `steps_skipped`) e roda no seguinte.
- Pool esgotado em qualquer passo passa a contar uma falha e pular os passos de banco seguintes, em vez de uma falha por passo.
- `mark_missing_objects` chamado fora do ciclo levanta na perda de conexão.
- Nenhum SQL nem definição do OpenObserve muda.

## Compliance

`tests/runs/test_maintenance_real_db_failures.py`, com Postgres real: os dois testes de pool esgotado e o `test_backend_encerrado_na_varredura_de_ausentes_conta_varredura_falha_e_passo_falho`.
