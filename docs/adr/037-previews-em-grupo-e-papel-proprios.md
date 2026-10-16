# ADR 037. Previews em `previews/`, papel `preview` e vínculo com a saída de origem

- **Estado:** proposto (2026-10-16), pendente de revisão do usuário no PR
- **Plano:** [1.6](../GoblinShiftPlan.md#s1-2), [1.7](../GoblinShiftPlan.md#s1-7), [2.2](../GoblinShiftPlan.md#s2-2), [3.3](../GoblinShiftPlan.md#s2-3), [3.6](../GoblinShiftPlan.md#s3-4)
- **Relacionado:** [ADR 019](019-layout-de-out-pacote-e-papeis.md), [ADR 007](007-storedobject-e-exclusao-adiada.md)

## Contexto

A tela de resultado mostra uma comparação (2.2) sem renderizar uploads brutos: imagens apenas derivadas e amostra CSV escapada de 100 registros ou 64 KiB. `GET /artifacts/{id}/preview` (1.6) entrega esse derivado com o tipo real só para imagem. O teto de 220 arquivos de 1.7 já conta "conteúdo/preview". Até aqui, nenhuma receita gerava previews e não havia como ligar um derivado à saída de que ele deriva.

## Decisão

- **Grupo `previews`** em `OutputGroup` (o ADR 019 pede que um grupo novo mude os dois lados, executor e coleta) e **`ItemOk.previews`**: `Preview(path, of)`, com `of` igual a uma saída do próprio item.
- **Opcionais e por último.** A receita guarda os previews em memória (no máximo 64 KiB a amostra e um JPEG de 512 x 512) e os grava depois de todo o conteúdo, só enquanto cabem nos tetos (`OutputDir.room_for`). Faltar espaço nunca falha a execução nem tira espaço do conteúdo.
- **Fora do pacote.** Manifesto e ZIP continuam só com o conteúdo e os relatórios; a semântica da `recipe_version` 1 não muda.
- **Formatos:** CSV Janitor gera a amostra (cabeçalho + até 100 registros, até 64 KiB, vírgula, fórmulas escapadas com tab); Image Forge gera JPEG de qualidade 80 que cabe em 512 x 512, com fundo branco onde havia alfa. O PDF Press gera, para cada documento, o trecho do mapa de páginas dele (`previews/<radical>.json`), sem renderizar PDF (2.2: "PDF por mapa de páginas"). O Downloads Detox não gera preview: ele não decodifica conteúdo (1.4), e decodificar só para o preview traria o PIL para a receita. A Media Mill entra com as miniaturas quando chegar à main.
- **Coleta:** cada preview tem de estar citado, em `previews/`, ser `.jpg` que começa como JPEG (até 512 KiB), `.csv` UTF-8 sem NUL ou `.json` com um objeto JSON estrito (os dois até 64 KiB); fora disso, `CollectionError`.
- **Banco (migração 0014):** papel `preview` e `artifact.source_artifact_id`, com FK composta ao dono em cascata e `CHECK ((role = 'preview') = (source_artifact_id IS NOT NULL))`.
- **API:** `GET /artifacts/{id}/preview` recebe o id da saída e entrega o preview dela: `image/jpeg` para a imagem, `application/octet-stream` para a amostra e o mapa de páginas, sempre com `attachment`, `nosniff`, CSP `default-src 'none'; sandbox`, CORP e `no-store`. Sem vaga de transferência: o preview é pequeno e lido inteiro.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Preview como saída comum (papel `output`) | Entraria no ZIP e no manifesto e seria baixado como resultado |
| Preview gerado pela API ou pelo worker | Decodificar imagem fora do sandbox; o PIL só existe no executor (ADR 018) |
| Previews gravados junto com cada saída | Um lote grande de imagens com miniaturas passaria do teto de 220 e falharia a execução |
| FK `RESTRICT` para a origem | A purga apaga artefatos por objeto, sem ordem; o preview não existe sem a origem |

## Consequências

- Image Forge com 100 imagens e miniaturas usa 200 arquivos; sobram 20 previews. Sem miniaturas, todos cabem.
- `GET /runs/{id}/results` passa a listar artefatos `preview` (valor novo no enum de saída `role`).
- Excluir as saídas (`DELETE /runs/{id}/artifacts`) e a retenção de 48 h tratam o preview como qualquer artefato da Run.

## Como verificar

- `tests/processing/test_previews.py`, `tests/processing/test_output_dir.py` (espaço), `tests/csv_janitor/test_preview.py`, `tests/image_forge/test_recipe.py` (preview), `tests/processing/test_executor.py` (fora do ZIP).
- `tests/runs/test_collection.py` (formatos, citação e teto) e `tests/runs/test_worker_flow.py` (publicação com origem).
- `tests/test_schema_constraints.py` (papel, origem obrigatória, outra conta) e `tests/api/test_artifact_preview.py` (tipo, cabeçalhos, 404 e 410).
