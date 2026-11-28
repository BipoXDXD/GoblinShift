# ADR 070. PDF Press v2: intervalos, rotação, compressão sem perdas e `strip_metadata`

- **Estado:** Aceito (2026-11-22, por decisão do dono: D1 a D7 = recomendações, plano `paridade-pdf-csv-detox-v2`)
- **Plano:** [1.5](../GoblinShiftPlan.md#s1-5) (PDF Press) e [1.6](../GoblinShiftPlan.md#s1-6) (versões, `deprecated` e `Sunset`)
- **Relacionado:** #564 (paridade com a concorrência), [ADR 035](035-pdf-press-com-pikepdf.md), [ADR 067](067-primeiras-versoes-2-das-receitas.md)

## Contexto

A pesquisa de concorrência apontou quatro lacunas do PDF Press v1: o `select` pede uma página por
item (um capítulo de 40 páginas vira 40 itens), não há rotação, não há compressão e os metadados
de anotação (autor, datas) passam para a saída. As quatro mudam schema, defaults ou semântica, e o
plano (1.6) fixa cada versão publicada; por isso entram numa v2, na forma do ADR 067.

## Decisão

- **Intervalos (D1).** O item do `select` passa a ser `{upload_id, first_page, last_page,
  rotation}`, com `last_page` obrigatório e `null` significando "até o fim". A API recebe a
  estrutura, nunca texto; o front lê `1-3, 10-` e monta a lista. A expansão em páginas mora no
  executor, o único que conhece a contagem de cada PDF: intervalo além do fim é
  `PDF_PAGE_NOT_FOUND`, com a página pedida. O teto de 400 vale para os itens na aceitação e para
  as páginas expandidas no executor, como antes.
- **Rotação (D2).** `rotation` em 0, 90, 180 ou 270, relativa: soma à `/Rotate` herdada da página
  (270 sobre 90 volta a 0). No `merge` e no `split` é global; no `select`, cada intervalo tem a
  sua, e a global explícita é recusada ("rotation só se aplica a merge e split").
- **Compressão (D5).** `compression` = `none` (default, bytes como na v1) ou `lossless`. A
  recompressão é nossa, em Python: só streams `/FlateDecode` sozinhos (nome ou lista de um) são
  decodificados, em blocos de 1 MiB, com dois tetos de bytes decodificados (64 MiB por stream e
  512 MiB por execução, constantes de `limits.py`, não publicados no catálogo). O stream que
  passa de um teto, que não fica menor ou que não decodifica fica como está; os `DecodeParms`
  (preditor PNG) são preservados. Depois, o qpdf grava com object streams gerados. O relatório
  traz `streams_recompressed` e `streams_kept`.
- **"Antes" e "depois".** O "antes" é a mesma composição gravada sem compressão, contada por um
  `_ByteCounter` que não guarda os bytes; o "depois" é o arquivo gravado. Os dois entram nos
  contadores do item (`bytes_before_compression`, `bytes_after_compression`), e a comparação do
  ADR 059 os publica quando há compressão.
- **`strip_metadata` (D3).** Ligado por padrão na v2: tira `/T`, `/M` e `/CreationDate` das
  anotações e dos `/Popup` delas. O `/Info` e o XMP do catálogo **nunca** saem, com ou sem a
  opção, porque o documento composto é novo desde a v1; um teste parametrizado prova isso nas
  duas posições.
- **`page-map.json` aditivo.** Cada página ganha `rotation` só quando diferente de zero; o mapa da
  v1, e o da v2 sem rotação, saem byte a byte iguais.
- **Versões.** `params_v2_from_mapping` e `PdfPressV2Parameters` são próprios; a receita
  (`PdfPress`, sem sufixo) e o escritor de documentos rodam as duas versões. A v1 gera os mesmos
  bytes de antes (SHA-256 fixo em teste). `upload_references` lê o `upload_id` de cada item do
  mapa canônico, sem parser de versão, e serve às duas. A v1 fica `deprecated` com `Sunset` em
  `sunset_after_merge(PDF_PRESS_V2_MERGE_DEADLINE)`, prazo de merge 2026-12-31: 2027-04-01. Um
  merge depois do prazo exige mover a constante. Os presets v1 abrem no formulário da v2 (ADR 067).

## Alternativas descartadas

- **Intervalo como texto na API** (`"1-3,10-"`): põe uma minilinguagem no contrato, com erros sem
  campo.
- **Expansão só no front:** o navegador não sabe quantas páginas cada PDF tem, então `10-` não
  existiria.
- **`recompress_flate` do qpdf:** decodifica também LZW, RunLength e filtros encadeados, sem teto
  de bytes decodificados; uma bomba de descompressão passaria.
- **Reamostrar imagens** (lacuna 22): muda conteúdo, é compressão com perdas e fica fora do escopo.

## Consequências

- O `lossless` custa CPU: 200 páginas com 245 MiB decodificados levaram 2,1 s com dados
  repetitivos e 41,8 s com alta entropia, pico de cerca de 485 MiB, dentro dos 10 min da tentativa.
- O snapshot `tests/catalog/congeladas/pdf-press-v2.json` trava schema e defaults da v2.
- Até o `Sunset`, convivem duas versões; depois dele a v1 sai do catálogo e os presets v1 só abrem
  pela Bancada, já como v2.
