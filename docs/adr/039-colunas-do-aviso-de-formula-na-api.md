# ADR 039. Contagem por coluna do aviso de fórmula no banco e na API

- **Estado:** proposto (2026-10-16), pendente de revisão do usuário no PR
- **Plano:** [1.3](../GoblinShiftPlan.md#s1-3), [1.7](../GoblinShiftPlan.md#s1-7)
- **Relacionado:** [ADR 019](019-layout-de-out-pacote-e-papeis.md), [ADR 037](037-previews-em-grupo-e-papel-proprios.md)

## Contexto

O plano (1.3) pede que o aviso de células que parecem fórmula informe a contagem por coluna e deixe claro que `-` gera muitos falsos positivos (números negativos). O CSV Janitor já conta por coluna, mas a contagem só existia no `detail` em texto do `report.json`. O banco e o `GET /runs/{id}` guardavam e devolviam apenas o código `CSV_FORMULA_CELLS`, porque a regra era "nada do arquivo no banco": o `detail` pode citar valores, e as contagens por item só têm nomes de contador nossos (`records_read`), nunca nomes de coluna.

## Decisão

- **Relatório:** `ReportWarning.columns` com `(nome, células)` de todas as colunas de saída, na ordem da saída. Só o aviso `CSV_FORMULA_CELLS` o preenche. No `report.json` ele vira `"columns": [{"name", "cells"}]`, sem mudar a `schema_version`, e o aviso sem colunas continua aceito (executor anterior).
- **Coleta e banco:** a coleta confere a forma (lista não vazia, nome texto não vazio, células inteiras ≥ 1) e guarda em `run_input.details.formula_cells` as **10** colunas de maior contagem (no empate, vale a ordem da saída) e `more_columns`, o número de colunas que ficaram de fora. O nome é cortado em **120** caracteres com `…`, e controle ou formato invisível (Cc, Cf) vira U+FFFD: o JSONB recusa NUL, e bidi disfarça o texto exibido. Cabeçalho vazio ou só de espaços vira **nome nulo**, em vez de derrubar a contagem inteira. O relatório não traz a posição da coluna, e um rótulo em texto como "(sem nome)" colidiria com uma coluna real de mesmo nome.
- **API:** `RunItemResponse.formula_cells` (aditivo), `null` sem o aviso. Os tetos aparecem na spec (`maxItems`, `maxLength`, `minimum`). A leitura do banco é tolerante: o que não tiver a forma esperada vira `null`.
- **Exceção registrada:** nomes de coluna são o único texto do arquivo no detalhe da Run. Valores de célula continuam fora.
- **Retenção:** some com `run_input.details` aos 30 dias, como avisos e contagens.
- **Web:** mostra o nome como texto (React, nunca HTML), cortado em 40 caracteres na tela, com a nota sobre o `-`. O nome nulo aparece como *coluna sem nome*, em itálico: o rótulo é da interface e não se confunde com um nome vindo do arquivo.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Mandar o `detail` do relatório para a API | Texto livre que pode citar valores de célula, e a tela não consegue estruturá-lo |
| Contagens por coluna em `counts` | `counts` só admite nomes de contador nossos (`ITEM_COUNT_NAME`), e nomes de coluna quebrariam essa allowlist |
| Guardar todas as colunas (até 100) | São até 100 nomes de até 64 KiB por item no banco. A tela só precisa das maiores, e o `report.json` do pacote tem todas |
| Ordem da saída em vez da maior contagem | Com o teto, o "e mais N" esconderia colunas mais afetadas que as mostradas |

## Consequências

- Nome de coluna passa a existir no banco por até 30 dias, só para o dono (o detalhe é filtrado por conta).
- Uma coluna de negativos aparece como suspeita. A nota sobre o `-` fica junto da lista.

## Como verificar

- `tests/runs/test_formula_cells.py` (ordem, tetos, substituição, coluna sem nome e leitura tolerante), `tests/runs/test_collection.py` (forma e executor anterior), `tests/runs/test_worker_flow.py` (gravado sem valor de célula).
- `tests/processing/test_csv_janitor_recipe.py` e `tests/processing/test_report_file.py` (relatório), `tests/api/test_runs.py` e `tests/test_openapi_limits.py` (contrato).
- `web/src/runs/RunItems.test.tsx` (texto, corte, coluna sem nome e nota).
