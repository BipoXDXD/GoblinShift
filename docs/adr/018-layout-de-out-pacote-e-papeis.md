# ADR 018. Layout de `out/` com um nível de grupos, pacote `<recipe_id>.zip` e papéis `manifest` e `package`

- **Estado:** aceito na etapa 0 da fatia 5 (2026-10-14), pendente de revisão do usuário no PR da fatia
- **Plano:** [1.2](../GoblinShiftPlan.md#s1-2), [1.4](../GoblinShiftPlan.md#s1-4), [1.7](../GoblinShiftPlan.md#s1-7), [3.2](../GoblinShiftPlan.md#s3-2), [3.3](../GoblinShiftPlan.md#s3-3), [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 005](005-area-do-executor-em-filesystem-fixo.md), [ADR 006](006-run-e-publicacao-multiagregado.md)

## Contexto

O Image Forge entrega `images/` e `thumbnails/`; o Downloads Detox, `images/`, `tables/`, `text/` e `archives/` (1.2, 1.4). Até a fatia 4, `out/` era plano: o `OutputDir` recusava separador no nome e a coleta do worker recusava qualquer diretório. A fatia 5 também traz o empacotador comum, que grava `manifest.json` e um ZIP em `out/`, e o banco só conhecia os papéis de artefato `output` e `report`.

## Decisão

- **Um nível de subpasta em `out/`, de lista fechada.** `OutputGroup` (`images`, `thumbnails`, `tables`, `text`, `archives`) é a única fonte dos nomes. A receita pede `OutputDir.create(nome, papel, posição, group=...)` e recebe o caminho relativo (`images/foto.jpg`), que é o que cita em `ItemOk.outputs`. O mesmo caminho é o nome dentro do ZIP e o `artifact.file_name` (até 240 bytes, como o CHECK já permitia).
- **Colisão por caminho completo.** NFC, `casefold()` e sufixo pelo índice original da entrada valem dentro do grupo; o mesmo nome em grupos diferentes não colide. Nomes reservados (`manifest.json`, `report.json` e o nome do pacote, passado pelo executor) só valem na raiz.
- **Coleta.** O worker aceita, em `out/`, arquivos regulares e diretórios reais com nome em `OutputGroup`; dentro deles, só arquivos regulares sem hardlink, sem segundo nível. Grupo desconhecido, symlink ou subpasta aninhada é `CollectionError`. Os tetos de 220 arquivos e 480 MiB somam a raiz e os grupos.
- **Pacote na raiz.** `manifest.json` e `<recipe_id>.zip` (`csv-janitor.zip`, `image-forge.zip`, `downloads-detox.zip`) ficam na raiz de `out/`, fora do orçamento de conteúdo (cada um com o seu teto de 1.7: 8 MiB e 512 MiB).
- **Papéis novos.** `ArtifactRole.MANIFEST = "manifest"` e `ArtifactRole.PACKAGE = "package"`; a migração 0005 só alarga o CHECK `artifact_role_check` (expand).

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| `out/` plano, grupo só no nome dentro do ZIP (mapa no relatório) | Dois nomes para o mesmo arquivo; `images/a.jpg` e `thumbnails/a.jpg` virariam colisão com sufixo |
| Árvore livre em `out/` | Coleta mais perigosa (profundidade, symlinks), sem necessidade |
| Manifesto e ZIP com o papel `output` | O download e a listagem de resultados não distinguiriam o pacote do conteúdo |

## Consequências

- Mudam os dois lados do contrato de `out/`: `OutputDir` no executor e a coleta no worker. Um grupo novo exige mudar `OutputGroup` (e, com isso, os dois lados de uma vez).
- O caminho mais longo possível (`thumbnails/` + 180 bytes = 191) fica abaixo dos 240 bytes de 3.6; um teste afirma isso.
- O download usa o último componente do `file_name` no `Content-Disposition` (integração da fatia 5).
- O enum `role` no OpenAPI ganha dois valores: mudança aditiva do contrato.

## Como verificar

- `tests/processing/test_output_dir.py`: grupos, colisões por caminho, reservas só na raiz, symlink no lugar do grupo, teto de caminho.
- `tests/runs/test_collection.py`: grupos aceitos, grupo desconhecido, segundo nível, symlink e hardlink no grupo, tetos somados.
- `tests/test_schema_constraints.py`: todo valor de `ArtifactRole` aceito pelo banco e papéis fora da lista recusados.
