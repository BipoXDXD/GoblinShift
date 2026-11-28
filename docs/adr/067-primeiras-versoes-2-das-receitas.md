# ADR 067. Primeiras versões 2 das receitas, começando pelo CSV Janitor

- **Estado:** Aceito (2026-11-22, por decisão do dono: D4 = a, D6 = a, D7 = a, plano `paridade-pdf-csv-detox-v2`)
- **Plano:** [1.3](../GoblinShiftPlan.md#s1-3) e [1.6](../GoblinShiftPlan.md#s1-6) (versões, `deprecated` e `Sunset`)
- **Relacionado:** #564 (paridade com a concorrência)

## Contexto

A pesquisa de concorrência apontou três lacunas do CSV Janitor: o CSV do Excel em português
chega em Windows-1252 e é recusado, a saída sem BOM abre no Excel com os acentos trocados, e
espaços internos repetidos não têm limpeza. As três mudam schema e defaults, e o plano (1.6)
fixa a semântica de cada versão publicada. Esta é a primeira versão 2 do projeto, e a forma
escolhida aqui vale para as próximas (Downloads Detox e PDF Press).

## Decisão

- **Modelo.** `CsvJanitorV2Parameters` herda de `CsvJanitorV1Parameters` só para reaproveitar os
  validadores. Ganha `input_encoding` (`utf-8`, `windows-1252`, `latin-1`; default `utf-8`),
  `output_bom` (default `false`) e `collapse_whitespace_columns` (default vazia). O congelamento
  de schema e defaults de cada versão é garantido por `tests/catalog/congeladas/`, não pela
  herança.
- **Parser.** Um `params_*_from_mapping` por versão, sem flag de versão. Os dois chamam o mesmo
  `_build` com o conjunto de opções da versão, e o da v1 continua recusando as opções da v2.
- **Receita.** A classe perde o sufixo (`CsvJanitor`) e roda as duas versões. A diferença mora
  nos `Params`, cujos campos novos têm default igual ao comportamento da v1.
- **Latin-1 (D4 = a).** Byte de controle C1 (0x80–0x9F) recusa o item com `UNSUPPORTED_FORMAT`
  e diz que o arquivo parece Windows-1252. BOM do UTF-8 com codificação de 8 bits também recusa.
- **`Sunset`.** A v1 passa a `deprecated` com `sunset_at` no primeiro dia do mês seguinte a
  (data do merge + 90 dias), calculado por `sunset_after_merge` a partir de um prazo de merge
  fixo (`CSV_JANITOR_V2_MERGE_DEADLINE`, 2026-11-30): 2027-03-01T00:00:00Z. A data não lê o
  relógio; um merge depois do prazo move o prazo, e o Sunset segue a regra.
- **Bancada.** O módulo da receita declara `reusableVersions: [1]`: preset ou execução da v1 abre
  no formulário da v2 com os parâmetros, sobre os defaults da v2.
- **Presets v1.** Pela API, um preset v1 continua rodando a v1 até a retirada, que fica para
  outro plano. Pela Bancada, ele abre no formulário da v2 e roda como v2, e "Atualizar preset" o
  grava como v2: é a migração a pedido do usuário, com a mesma semântica, porque os defaults dos
  campos novos reproduzem a v1.

### Downloads Detox v2

A v2 do Detox muda só a semântica, sem mudar o schema: aceita também PDF, WebP e GIF (lacuna 5
da pesquisa), e as mesmas entradas que a v1 recusava com `UNSUPPORTED_FORMAT` passam a ser
copiadas. Por isso é uma versão nova, mesmo com os mesmos parâmetros.

- **Modelo.** `DownloadsDetoxV2Parameters` herda de `DownloadsDetoxV1Parameters` e troca só o
  título, para o contrato distinguir as variantes. Schema e defaults são os da v1, e o snapshot
  `downloads-detox-v2.json` os congela à parte.
- **Parser.** O mesmo `params_from_mapping` nas duas versões: não há opção nova a recusar.
- **Receita.** `DownloadsDetox(accepted=...)`. A diferença entre as versões é o conjunto de
  formatos aceitos (`V1_FORMATS` ou `V2_FORMATS`), passado no registro. Extensão e assinatura de
  formato fora do conjunto não são reconhecidas, e a mensagem de recusa da v1 fica idêntica.
- **Assinaturas.** Só no primeiro bloco lido, sem abrir nem decodificar: `%PDF-`, `GIF87a` ou
  `GIF89a`, e RIFF com a forma `WEBP` (o RIFF de WAV e AVI não passa).
- **Pasta.** PDF é do tipo `document` e vai para `documents/` (`OutputGroup.DOCUMENTS`); a coleta
  do worker deriva a lista fechada de `OutputGroup`, então executor e worker saem do mesmo commit.
- **PDF opaco.** Como o ZIP, o PDF é copiado sem inspeção. O item leva uma nota, e o relatório da
  versão que aceita PDF declara que JavaScript, anexos e formulários só são recusados pelo PDF
  Press. O relatório da v1 não ganha chave nova.
- **`Sunset`.** Prazo de merge das v2 desta onda (`DOWNLOADS_DETOX_V2_MERGE_DEADLINE`,
  2026-12-31), o que dá 2027-04-01T00:00:00Z pela mesma `sunset_after_merge`.
- **Bancada.** `reusableVersions: [1]`, como no CSV Janitor.

## Alternativas descartadas

- **Copiar o modelo inteiro na v2:** duplica os validadores, e uma correção numa cópia esquece
  a outra.
- **`params_from_mapping(raw, version=...)`:** um parâmetro que escolhe a lógica, e a v1 deixaria
  de ser uma função congelada que se lê sozinha.
- **Migrar presets v1 para v2 no banco:** muda dado do usuário sem pedido.

## Consequências

- O catálogo lista as duas versões. `POST /runs`, `POST /presets` e o detalhe da versão com a v1
  respondem com `Sunset`.
- Quem usa a API com a v1 tem até o `Sunset` para trocar `recipe_version`. Os parâmetros da v1
  são parâmetros v2 válidos, com o mesmo resultado.
- O contrato só cresce: uma variante nova no `oneOf` do corpo, e o `oasdiff breaking` não aponta
  quebra.
