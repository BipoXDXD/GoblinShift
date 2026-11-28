# ADR 059. Comparação antes/depois por entrada no banco e na API

- **Estado:** Aceito (2026-11-22, por decisão do dono: D2 = a)
- **Plano:** [2.2](../GoblinShiftPlan.md#s2-2), [1.7](../GoblinShiftPlan.md#s1-7)
- **Relacionado:** [ADR 039](039-colunas-do-aviso-de-formula-na-api.md) (mesmo caminho do `formula_cells`)

## Contexto

As receitas já medem antes e depois de cada entrada, mas a medida ficava só no `report.json`: a coleta descartava `details`, e o `GET /runs/{id}` devolvia só códigos e `counts`. A tela não tinha como mostrar "2400 × 1600 → 1200 × 800" nem "12 → 10 páginas" sem abrir o pacote. O executor é não confiável: o que vem do relatório é entrada externa, e o detalhe da Run não leva texto do arquivo.

## Decisão

- **Só o que a receita já mede**, por receita:

  | Receita | `kind` | Antes → depois | Origem |
  |---|---|---|---|
  | CSV Janitor | `table` | registros `records_read` → `written` | `counts` |
  | Image Forge | `image` | dimensões, formato (`jpeg`, `png`, `mpo`) e tamanho em bytes | `details`; `bytes_after` vem da coleta |
  | PDF Press | `pdf` | páginas `pages` → `output_pages` | `counts` |
  | Media Mill | `media` | dimensões do vídeo → da miniatura; codec de áudio de origem → formato extraído | `details` |
  | Downloads Detox | — | nenhuma: os bytes são preservados e o que muda é o nome, que não pode ir para este campo | — |

- **D2 = (a):** no Image Forge, `bytes_after` é o tamanho da saída de `images/` que o worker já mede na coleta (`OutputFile.size` da primeira saída do próprio item, pelo nome). Não há I/O novo. Sem medida, sai nulo.
- **Só listas fechadas:** a comparação carrega inteiros e valores de `StrEnum` próprios (`ImageFormat`, `AudioCodec`, `AudioFormat`), nunca nome de arquivo, nome de coluna ou texto livre. `comparison_from_json` é a única validação: chaves exatas, inteiros ≥ 0, dimensões ≥ 1, pares inteiros ou ausentes. O que foge da forma vira nulo, e o item segue como antes.
- **Montagem na coleta:** `collect_outputs` monta a comparação a partir de `counts` e `details` e o worker grava `run_input.details.comparison`. O executor e o `report.json` não mudam.
- **API:** `RunItemResponse.comparison` (aditivo), uma união discriminada por `kind` (`TableComparisonResponse`, `PdfComparisonResponse`, `ImageComparisonResponse`, `MediaComparisonResponse`), com os pisos na spec (`minimum`). A leitura do banco passa de novo por `comparison_from_json`: Run antiga (sem a chave) ou forma inválida responde `null`.
- **Retenção:** some com `run_input.details` aos 30 dias, como avisos e contagens.
- **PDF Press v2 com compressão (ADR 070):** o PDF compara também o tamanho, `bytes_before` (sem compressão) → `bytes_after` (com compressão), lidos de `bytes_before_compression` e `bytes_after_compression` em `counts`. O par só entra com as duas chaves; com uma só, as páginas ficam e o tamanho sai nulo. As duas chaves são opcionais em `comparison_from_json`, para que a linha gravada antes da v2 continue válida, e o par pela metade invalida a comparação, como no Media Mill. Na API, os dois campos são opcionais e anuláveis (aditivo).

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Mandar `details` inteiro para a API | Texto livre e chaves que mudam de receita para receita e de versão para versão |
| Mapa `dict[str, int \| str]` sem tipo | O front não teria união discriminada, e o texto livre voltaria pela porta dos fundos |
| Campo novo no `report.json` | Exigiria nova imagem do executor, e a coleta já tem o que precisa |

## Consequências

- A tela de resultado mostra antes → depois sem baixar o pacote; o Detox continua sem comparação.
- Uma regra a mais na coleta, específica do Image Forge (`bytes_after`).
- Formato novo na v2 de uma receita exige acrescentar o valor ao enum da comparação; até lá, a entrada sai sem comparação, sem erro.

## Como verificar

- `tests/runs/test_comparison.py` (forma, enums e desvio por receita) e os testes de desvio em `tests/image_forge`, `tests/media_mill`, `tests/pdf_press` e `tests/processing/test_csv_janitor_recipe.py`.
- `tests/runs/test_collection.py` (montagem e `bytes_after`) e `tests/runs/test_worker_flow.py` (gravado em `details`).
- `tests/api/test_runs.py` (contrato, Run antiga, `details` anulado e forma inválida) e `tests/test_openapi_export.py`.
