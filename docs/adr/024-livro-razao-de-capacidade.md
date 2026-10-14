# ADR 024. Livro-razão de capacidade em módulo próprio

- **Estado:** aceito na Fase 0 da fatia 6 (2026-10-14), decisões D-3, D-4 e D-5 do plano da fatia
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [3.2](../GoblinShiftPlan.md#s3-2), [3.3](../GoblinShiftPlan.md#s3-3)
- **Relacionado:** [ADR 007](007-storedobject-e-exclusao-adiada.md), [ADR 012](012-servico-aberto-com-cotas.md)

## Contexto

1.7 limita o armazenamento a 4 GiB por conta e 20 GiB no ambiente, e pede reservar capacidade antes de aceitar trabalho, inclusive o teto de saída de uma Run. A reserva do upload acontece em `files`; a da Run, em `runs`. `files` não pode importar `runs`, e o livro-razão precisa ser um só para a soma valer.

## Decisão

- **Módulo novo `capacity`**, uma camada abaixo de `files`, `identity` e `catalog` e acima de `platform` (contrato `layers` do import-linter). `files` e `runs` o chamam; ele não conhece nenhum dos dois.
- **Tabelas** (migração 0006): `capacity_pool` (escopo `global` ou `account`, recurso, `max_amount`, `reserved`, com `CHECK (reserved BETWEEN 0 AND max_amount)` e `UNIQUE NULLS NOT DISTINCT (scope, owner_id, resource)`) e `resource_reservation` (uma linha por reserva, com `released_at`, e no máximo uma reserva viva por detentor e recurso). O pool global nasce na migração; o da conta, sob demanda.
- **Detentores** (D-5): `upload` na intenção, convertido em `object` no `ready`; `run` com o teto de saída, convertido em `object` na publicação e liberado no resto; `object` liberado no purge físico.
- **Fila global, execução ativa e transferências** (D-4) continuam nos mecanismos atuais (advisory lock com contagem, índice único parcial e `transfer_slot`), já atômicos e testados no caos da fatia 4.
- **`holder_id` sem FK.** O detentor é polimórfico (`holder_kind`). É a associação polimórfica do SQL Antipatterns, aceita de propósito porque o livro-razão é interno, sem JOIN de negócio, e a manutenção o reconcilia com os objetos reais.
- **Limites por classe de rota** (120, 60 e 10 por minuto, D-9) ficam para a fatia 8, com o teste de carga; a classe de cada rota já existe na telemetria.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Livro-razão dentro de `files`, com `runs` chamando `files` para a reserva da Run | Mistura a cota da Run com o segredo de `files` |
| Livro-razão em `runs` | `files` não pode importar `runs` |
| Fila e execução ativa como linhas do pool | Troca um mecanismo correto e testado por outro, sem ganho |
| Reservar só bytes reais no `committed` | 1.7 pede reservar antes de aceitar; recusaria tarde |

## Consequências

- Dois mecanismos de capacidade convivem: o pool para quantidades e as travas atuais para contagens.
- Uma Run reserva cerca de 1 GiB de teto; com 4 GiB por conta sobram cerca de 3 GiB para uploads enquanto ela roda.
- Durante o deploy da 0006, a API anterior cria uploads sem reserva; a reconciliação os adota.
- A purga da conta chama `capacity.purge_owner` antes de apagar `app_user` (FK `RESTRICT`).

## Como verificar

- `lint-imports`: contrato de camadas com `goblinshift.capacity`.
- `tests/test_schema_fatia_6.py`: CHECKs, unicidade com `NULLS NOT DISTINCT`, uma reserva viva por detentor e o pool global de 20 GiB.
- Frente D da fatia 6: reserva acima do teto da conta → 429 `QUOTA_EXCEEDED`; do ambiente → 503; duas reservas simultâneas não passam do teto; propriedades do livro-razão com Hypothesis.
