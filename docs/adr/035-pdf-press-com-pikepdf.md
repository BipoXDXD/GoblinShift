# ADR 035. PDF Press com pikepdf/qpdf no executor, falha da execução inteira e mapa de páginas

- **Estado:** proposto no PR do PDF Press (2026-10-15), pendente de revisão do usuário
- **Plano:** [1.5](../GoblinShiftPlan.md#s1-5), [1.6](../GoblinShiftPlan.md#s1-6), [1.7](../GoblinShiftPlan.md#s1-7), [2.2](../GoblinShiftPlan.md#s2-2), [3.6](../GoblinShiftPlan.md#s3-6); [D13](../GoblinShiftReferencias.md#d-3)
- **Relacionado:** [ADR 004](004-container-gvisor-por-tentativa.md), [ADR 018](018-pillow-na-imagem-do-executor.md), [ADR 019](019-layout-de-out-pacote-e-papeis.md), [ADR 021](021-digest-da-imagem-em-context-json.md)

## Contexto

O plano define o PDF Press (`pdf-press`) com pikepdf/qpdf: até 20 PDFs não criptografados e 200 páginas de entrada; `merge`, `split` (páginas ou grupos de tamanho fixo) e `select` (lista ordenada de `{upload_id, page_number}`, repetições preservadas); até 400 páginas e 200 documentos de saída; sem OCR, senha, formulários, assinaturas, anexos nem conteúdo ativo; e, diferente das três receitas centrais, arquivo danificado, página inexistente ou aviso estrutural não reconhecido falham a execução inteira. O que o plano não fixava: a biblioteca exata e onde ela roda, os códigos de falha, como o executor liga um `upload_id` a uma entrada (o `job.json` só tem posições), o formato do mapa de páginas, o teto de bytes do lote e o que conta como "conteúdo ativo detectado".

## Decisão

- **pikepdf 10.16.0, com o libqpdf 12.4.2 da própria wheel, só no executor.** Pin exato em `[project.dependencies]` e no grupo `executor` (que passa a ter Pillow e pikepdf; lxml e packaging vêm como dependências e entram no lock com hash). A imagem é a mesma de [ADR 018](018-pillow-na-imagem-do-executor.md), fixada por digest. A 10.16.0 tinha 16 dias no PR; a 10.17.0, só 4. O contrato "Só o executor carrega o pikepdf" do import-linter proíbe o import fora de `processing`. A inspeção roda só no executor, sob gVisor com 2 GiB, 1 vCPU e 10 min; a API e o worker não abrem PDF, nem na classificação do upload.
- **Inspeção conservadora, por varredura de todos os objetos** (inclusive os que nenhuma página alcança):
  - `%PDF-<versão>` no byte 0; abertura sem recuperação e sem herança de atributos (`attempt_recovery=False`, `inherit_page_attributes=False`); qualquer aviso do qpdf, inclusive o que só aparece ao ler os bytes crus de cada stream, é `PDF_INVALID`. Nenhum aviso é "reconhecido" como inofensivo na v1;
  - senha, criptografia (inclusive só com senha de dono), JavaScript, ações fora de `GoTo` e `URI` (em `/A`, `/OpenAction` e encadeadas em `/Next`), `/AA`, AcroForm, XFA, widgets, assinaturas e certificação, anexos (`/EmbeddedFiles`, `/EF`, `/AF`, `FileAttachment`), multimídia e portfólio são `PDF_UNSUPPORTED_FEATURE`. Um `/OpenAction` que é só destino de página (array) não é ação e passa;
  - teto de objetos pelo xref real (não pelo `/Size` declarado) e árvore de páginas andada à mão antes do `pdf.pages` do qpdf, pela regra do qpdf (`/Kids` decide: nó com `/Kids` é `/Pages`, sem `/Kids` é `/Page`), com profundidade até 32, cada nó visitado uma vez, `/Kids` maior que o resto do teto recusado antes de ser lido e `/Count` conferido. Isso veio de dois achados da revisão de segurança.
- **Códigos novos, todos de falha determinística da execução** (1.6, mudança aditiva no enum de saída): `PDF_INVALID`, `PDF_UNSUPPORTED_FEATURE`, `PDF_PAGE_NOT_FOUND` e `INPUT_LIMIT_EXCEEDED` (páginas somadas acima de 200 ou objetos acima de 100 mil num documento). Página ou documento de saída acima do teto usa o `OUTPUT_LIMIT_EXCEEDED` que já existia.
- **`upload_id` chega ao executor pelo `context.json`** (`input_upload_ids`, na ordem das posições), não pelo `job.json`: o leitor do job exige chaves exatas, e um executor anterior fixado numa Run recusaria o job; o `context.json` é tolerant reader ([ADR 021](021-digest-da-imagem-em-context-json.md)). `InputFile` ganha `upload_id` (aditivo). A aceitação recusa com `422 VALIDATION_FAILED`, no campo `parameters.select.N.upload_id`, a página de um upload que não está em `input_upload_ids` (`RecipeVersion.upload_references`); nunca 404, como 1.6 manda para IDs do corpo. Sem entradas (`validate_only`, presets) não há o que comparar.
- **Saídas na raiz de `out/`**, sem grupo novo: `merged.pdf`, `selection.pdf` ou `<nome>-p001-003.pdf` por parte do split, e `page-map.json`. Cada documento é atribuído, no relatório e na linhagem do manifesto, à entrada da sua primeira página; o mapa, à primeira entrada. Não muda a lista fechada de grupos do ADR 019.
- **Mapa de páginas como arquivo de conteúdo** (`page-map.json`, `schema_version` 1): entradas (posição, nome, páginas) e documentos (caminho e cada página com `input_position` e `page_number`, na ordem). Entra no ZIP e no manifesto como qualquer saída, sem mudar o schema do relatório nem do manifesto.
- **Composição com o qpdf, sem decodificar streams** (`stream_decode_level=none`), com `/ID` determinístico e a maior versão de PDF entre as entradas. O documento é novo: referências a páginas não copiadas viram `null`, então um link não arrasta para a saída uma página não escolhida.
- **Teto do lote de 256 MiB** (`max_total_input_bytes`, imposto na aceitação): o merge copia quase todos os bytes de cada entrada, e a metade dos 480 MiB de conteúdo deixa folga ao split, que repete recursos compartilhados em cada parte.
- **Interface:** cartões numerados (PDF e página) para a seleção, reordenáveis por botões (WCAG 2.5.7) e por arrasto; a lista de arquivos mostra a posição quando a ordem muda o resultado; a prévia é o mapa de páginas previsto, sem renderizar PDF.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Rejeitar o PDF ruim por item e seguir com os outros | 1.5 manda falhar a execução inteira: uma composição sem um documento pareceria sucesso |
| `upload_id` como chave nova no `job.json` | O executor anterior, fixado em Runs já aceitas, recusaria todo job com a chave (ADR 021) |
| Traduzir `select` para posições na aceitação | Os parâmetros gravados e o hash de idempotência deixariam de ser os que o cliente enviou |
| Recusar todo `/OpenAction` | Destino de página na abertura é comum (LaTeX/hyperref) e não executa nada; ação no `/OpenAction` continua recusada |
| Remover o conteúdo ativo (`pikepdf.sanitize`) e seguir | 1.5 manda recusar o detectado; consertar seria prometer sanitização universal |
| Grupo novo `documents/` em `out/` | Mudaria a lista fechada do ADR 019 e da coleta, ponto que a Media Mill também toca; a raiz já aceita conteúdo |
| Mapa de páginas dentro do `report.json` | `details` só aceita valores simples; um campo novo mudaria o schema do relatório e a coleta |
| Endpoint de prévia (`GET /artifacts/{id}/preview`) | Não existe para nenhuma receita; a prévia do PDF é o mapa previsto na Bancada e o `page-map.json` no resultado |

## Consequências

- A imagem do executor ganha o qpdf (C++), o lxml (libxml2) e o packaging: superfície maior, limitada pela inspeção sem recuperação, pelo gVisor e pelos limites do container. O lxml não é importado no caminho do PDF Press (o pikepdf o carrega só para XMP).
- O manifesto do PDF Press traz `upload_id` dentro de `parameters` quando a operação é `select`: os parâmetros são os que o cliente enviou. As entradas continuam citadas por posição.
- Um PDF legítimo com formulário, assinatura ou anexo é recusado inteiro; a mensagem diz o motivo, e a pessoa precisa achatá-lo antes. A falha da execução não diz qual entrada nem qual página (o detalhe fica no `failure.json`, que não é persistido); o código basta para a interface explicar.
- O resultado é um documento novo: marcadores, estrutura de acessibilidade, formulários e assinaturas não são preservados (1.5).

## Compliance

- `tests/pdf_press/`: parâmetros, plano, inspeção com PDFs maliciosos gerados no teste (JavaScript nomeado, órfão e dentro de object stream, `/OpenAction`, `Launch`, `SubmitForm`, `GoToR`, `/Next`, `/AA`, anexos, formulários, XFA, widget, assinatura, multimídia, portfólio, senha, criptografia, truncado, xref quebrado, `/Length` errado, bombas de objetos e de páginas, árvore com ciclo, profunda, com `/Count` mentiroso e com `/Type` e `/Kids` divergentes), receita, executor e modelo do catálogo.
- `tests/test_dependencies.py` e `tests/test_executor_image.py` (também sob `runsc` no job `gvisor`): versões fixadas, distribuições da imagem, merge e ZIP determinístico, e PDFs hostis falhando deterministicamente com 2 GiB e 1 vCPU.
- `uv run lint-imports`: "Só o executor carrega o pikepdf", independência e "Receitas não empacotam" com o `pdf_press`.
- `scripts/e2e-pdf-press.sh` contra a pilha real; jornada do Playwright em `web/e2e/specs/journeys.spec.ts`.
