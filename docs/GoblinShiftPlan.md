# GoblinShift — especificação técnica

> **API de processamento de arquivos · Python / FastAPI · 12 de outubro de 2026 · revisado em 13 de outubro de 2026**

<a id="sumario"></a>
## Sumário

- **[1. API e funcionalidades](#api)**
  - [1.1. Capacidades compartilhadas](#s1-1)
  - [1.2. Image Forge — preparar imagens em lote](#s1-2)
  - [1.3. CSV Janitor — limpar tabelas sem alterar seu significado por adivinhação](#s1-3)
  - [1.4. Downloads Detox — organizar e empacotar arquivos](#s1-4)
  - [1.5. Processadores complementares](#s1-5)
  - [1.6. Contrato HTTP](#s1-6)
  - [1.7. Limites e disponibilidade dos arquivos](#s1-7)
  - [1.8. Serviço aberto: contas, abuso e privacidade](#s1-8)
  - [1.9. Glossário](#s1-9)
- **[2. Stack](#stack)**
  - [2.1. Componentes escolhidos](#s2-1)
  - [2.2. Frontend e experiência de operação](#s2-2)
  - [2.3. Notas de implementação da stack](#s2-3)
- **[3. Arquitetura](#arquitetura)**
  - [3.1. Monólito modular, processos separados](#s3-1)
  - [3.2. Módulos e dependências](#s3-2)
  - [3.3. Modelo persistido](#s3-3)
  - [3.4. Trabalho durável e publicação única](#s3-4)
  - [3.5. Estados, cancelamento e recuperação](#s3-5)
  - [3.6. Arquivos, identidade e isolamento](#s3-6)
  - [3.7. Verificação técnica e diagnóstico](#s3-7)
  - [3.8. Implantação na VPS](#s3-8)
  - [3.9. Ordem de construção em fatias](#s3-9)
  - [3.10. Decisões registradas](#s3-10)
- **[Referências: livros e documentação oficial](GoblinShiftReferencias.md)**

---

<a id="api"></a>
## 1. API e funcionalidades

O GoblinShift processa arquivos por receita e entrega resultados privados com manifesto. Interface web e clientes HTTP usam a mesma API.

**Objetivo:** projeto de portfólio com código público e, ao mesmo tempo, serviço aberto: qualquer pessoa com conta Google ou GitHub pode usar, dentro de cotas. Hospedagem em uma VPS própria (Hostinger), descrita em [3.8](#s3-8). Ser aberto torna abuso, custo fixo de hospedagem e privacidade requisitos de produto, tratados em [1.8](#s1-8).

O produto tem cinco receitas: **Image Forge, CSV Janitor, Downloads Detox, PDF Press e Media Mill**. Todas fazem parte do escopo; não há MVP parcial nem receita deixada como extensão futura. O catálogo anuncia apenas processadores implementados: o PDF Press já está na `main` ([ADR 035](adr/035-pdf-press-com-pikepdf.md)) e o Media Mill está no PR #74, ainda aberto (ADR 036).

*Decisão substituída em 2026-10-16:* o texto de 12 de outubro chamava o PDF Press e o Media Mill de "extensões independentes" que não bloqueavam as receitas centrais e só entrariam "com motivo concreto". Em 2026-10-16 o usuário decidiu o escopo completo ("nada de v1, v2... vamos implementar tudo"), e as duas receitas passam a ter fatia própria em [3.9](#s3-9).

Uma **receita** reúne operações fixas; o **preset** armazena parâmetros; a **execução** registra entradas, configuração, versão, estado e resultados. Sem editor de grafos, scripts de cliente ou IA no processamento.

<a id="s1-1"></a>
### 1.1. Capacidades compartilhadas

| Capacidade     | Comportamento da API                                                                                                                                             |
|----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Catálogo       | Informar receitas executáveis, formatos, parâmetros, defaults e limites efetivos.                                                                                |
| Upload         | Receber bytes por streaming, calcular SHA-256, confirmar persistência e manter o original imutável.                                                              |
| Configuração   | Validar parâmetros específicos da receita; recusar campos desconhecidos e combinações incompatíveis.                                                             |
| Execução       | Aceitar trabalho duravelmente e devolver um identificador sem esperar o processamento terminar.                                                                  |
| Acompanhamento | Informar estado, fase, tentativa atual, contagens e erros por arquivo.                                                                                           |
| Resultados     | Disponibilizar arquivos individuais, ZIP, manifesto e relatório, sempre com autorização.                                                                         |
| Presets        | Salvar, listar, editar e excluir configurações privadas, sem guardar arquivos dentro do preset.                                                                  |
| Histórico      | Consultar execuções por receita, estado e período; repetir uma configuração mediante nova execução.                                                              |
| Cancelamento   | Registrar a solicitação e interromper o executor; diferenciar solicitação recebida de término confirmado.                                                        |
| Exclusão       | Bloquear novos acessos na hora; a remoção física é adiada até não haver uso ativo (execução ou download em andamento). Excluir nunca falha por "arquivo em uso". |

**Publicação:** apenas após concluir a tentativa e validar saídas; temporários e resultados de tentativas falhas não viram downloads.

**Falhas por item:** nas três receitas centrais, arquivo inválido é rejeitado com código e motivo; os demais continuam. Havendo resultados válidos e rejeições, a execução termina `succeeded_with_warnings`. Se todos forem rejeitados, termina `failed`, com `NO_VALID_OUTPUTS`. Falha de infraestrutura, integridade, isolamento ou limite global faz a execução falhar; não é convertida em rejeição silenciosa.

**Rastreabilidade:** registrar entrada, operação, parâmetros efetivos e implementação; manter a ordem das entradas, não a de conclusão. O conteúdo exato do manifesto e do pacote está em [3.6](#s3-6); cada receita lista só o que acrescenta ao relatório.

<a id="s1-2"></a>
### 1.2. Image Forge — preparar imagens em lote

**Entrada:** JPEG/PNG estáticos de 8 bits: RGB, RGBA, cinza e paleta convertível. Rejeitar animação (inclusive PNG animado), JPEG CMYK, profundidade ou formato incompatível. MPO não é animação: é o formato de várias fotos de celular, e é aceito pelo quadro principal.

| Parâmetro                 | Valores e default                                              |
|---------------------------|----------------------------------------------------------------|
| `max_width`, `max_height` | Inteiros de 16 a 4096; default `1600` em ambos.                |
| `output_format`           | `preserve`, `jpeg`, `png`; default `preserve`.                 |
| `jpeg_quality`            | Inteiro de 60 a 95; default `85`. Usado apenas em saídas JPEG. |
| `jpeg_background`         | Cor hexadecimal `#RRGGBB`; default explícito `#FFFFFF`.        |
| `make_thumbnails`         | Booleano; default `false`.                                     |

**Processamento:** validar → orientar por EXIF → redimensionar → converter, se pedido → remover metadados suportados → miniatura opcional → relatório/ZIP.

O ajuste equivale a `contain`, sem corte nem ampliação, com filtro de reamostragem fixo e registrado no relatório. Após aplicar a orientação, calcular `escala = min(max_width / largura, max_height / altura, 1)`; arredondar cada dimensão para baixo, com mínimo de um pixel. A miniatura cabe em 320 × 320, também sem ampliar: não é necessariamente quadrada.

PNG preserva transparência. Na conversão para JPEG, compor a transparência sobre o fundo configurado antes de salvar. A interface mostra esse fundo; não há conversão acidental para preto. A qualidade JPEG não é aplicada como se fosse qualidade PNG.

**Modos de imagem:** `RGB`, `RGBA` e `L` são processados como estão; `LA` (cinza com transparência) segue as regras de transparência do RGBA; `P` é convertido para `RGBA` se tiver transparência (`tRNS`) e para `RGB` se não tiver; `I;16` e outros modos de 16 bits são rejeitados com `UNSUPPORTED_BIT_DEPTH`, coerente com a entrada de 8 bits. **Fotos de celular:** de um MPO, só o quadro principal (o primeiro) é processado; os demais quadros (profundidade, miniaturas) são descartados, e o relatório registra isso.

**Perfis de cor**, por uma lista fechada:
- sem perfil: tratado como sRGB, com essa suposição registrada;
- sRGB: aceito como está;
- Display P3, padrão de iPhones ao exportar JPEG: convertido para sRGB com intenção colorimétrica relativa, e a conversão fica registrada no relatório do arquivo. Cores fora do gamut sRGB são comprimidas, o que a interface informa;
- qualquer outro perfil, ou metadados de cor conflitantes: rejeitado com `UNSUPPORTED_COLOR_PROFILE`. Nada é convertido sem estar nesta lista e sem ficar registrado. Aplicar a orientação antes de remover EXIF. A remoção de metadados não é anunciada como anonimização. Os testes reabrem cada saída para confirmar quais metadados ficaram.

**Saídas:** imagens em `images/`, miniaturas opcionais em `thumbnails/`, dimensões/bytes anteriores e posteriores, formato, rejeições, manifesto e ZIP. Sem deduplicação implícita.

**Exemplos:** 2400 × 1200 vira 1600 × 800; 200 × 100 não amplia. Uma imagem inválida não bloqueia as demais; saída pode aumentar em bytes.

**Limite de pixels:** o teto de 1.7 é verificado antes de decodificar a imagem; acima dele, o arquivo é rejeitado com `IMAGE_TOO_LARGE`.

Como isso é feito com o Pillow está em [2.3](#s2-3).

<a id="s1-3"></a>
### 1.3. CSV Janitor — limpar tabelas sem alterar seu significado por adivinhação

**Entrada:** CSVs independentes em UTF-8 (BOM opcional), com cabeçalho e delimitador vírgula, ponto e vírgula ou tab. Sem Excel, JSON tabular, joins ou inferência de tipos. Quantidade, tamanhos e limites de campo e registro estão em 1.7.

**Campos sempre textuais:** `00123` e números longos são preservados; vazio continua `""`. Não inferir datas, booleanos, números ou `null`.

| Opção            | Contrato                                                                                                                                                                                                                                                                                |
|------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Delimitadores    | `input_delimiter` e `output_delimiter`; default vírgula, ambos explícitos na configuração.                                                                                                                                                                                              |
| Espaços externos | `trim_columns`: lista de colunas; default vazia. Não alterar espaços internos.                                                                                                                                                                                                          |
| Caixa            | `case_rules`: coluna e modo `lower` ou `upper`; default vazio. Sem regras conflitantes para a mesma coluna. A transformação pode mudar o tamanho do texto (ß → SS); os limites de campo e registro valem também para a saída, e excedê-los rejeita a tabela com `FIELD_LIMIT_EXCEEDED`. |
| Filtros          | Até 5 predicados combinados por AND: `eq`, `ne`, `contains`, `is_empty`. Valores comparados como texto; sem regex ou expressões livres.                                                                                                                                                 |
| Duplicatas       | `deduplicate_by`: uma ou mais colunas; lista vazia desativa. Preservar a primeira ocorrência.                                                                                                                                                                                           |
| Colunas finais   | `output_columns`: seleção e ordem explícitas; ausência preserva todas na ordem original. Lista vazia é inválida.                                                                                                                                                                        |

Os limites de campo e registro são contados em bytes UTF-8, na entrada e na saída. Exceder qualquer limite de tabela de 1.7 (tamanho, registros, colunas, registro ou campo) rejeita a tabela com `FIELD_LIMIT_EXCEEDED`, e o motivo diz qual limite foi; um arquivo que não é UTF-8 é rejeitado com `UNSUPPORTED_FORMAT`. Os filtros mantêm os registros que satisfazem todos os predicados. `trim_columns` remove das pontas qualquer espaço Unicode (`str.strip`).

A ordem é fixa:

```text
Ler estrutura → normalizar colunas selecionadas → filtrar registros
              → deduplicar → selecionar/ordenar colunas → serializar
```

Validar todas as referências de coluna contra cada cabeçalho antes de transformar aquela tabela. Cabeçalhos vazios ou duplicados são inválidos; não renomeá-los automaticamente. A regra de caixa utiliza as operações Unicode da versão Python fixada, sem depender do locale do servidor. Cabeçalhos não são normalizados junto dos valores.

Deduplicar por tuplas de valores, após normalizar/filtrar e antes da projeção. Preservar ordem dos sobreviventes; nunca concatenar campos com separador ambíguo.

**Estrutura:** o parser trata aspas, delimitadores internos e campos com quebras de linha.
- Número irregular de campos, campo entre aspas que não fecha até o fim do arquivo, coluna inexistente ou limite excedido rejeitam **a tabela inteira**. Não tentar recuperar linhas quando a fronteira de registro ficou ambígua.
- Aspas dentro de um campo sem aspas (`ab"c`) são aceitas como texto literal; só aspas que deixam a fronteira do registro ambígua rejeitam a tabela.
- Linhas fisicamente vazias fora de campos são ignoradas e contadas separadamente; um registro com campos vazios continua sendo registro.
- Exceção: numa tabela de **uma coluna**, uma linha vazia e um registro com o único campo vazio são indistinguíveis, e a linha vazia conta como registro, porque descartar dados é pior que preservá-los.
- Relatórios apontam posições pelo índice do registro, não pela linha física.

Como isso é feito com o módulo `csv` está em [2.3](#s2-3).

**Saídas:** um CSV UTF-8 válido por entrada, sem BOM, com cabeçalho, terminador de linha `\r\n` (RFC 4180) e aspas só quando necessárias, no delimitador configurado; contagens, manifesto e ZIP (na fatia 3, só os CSVs e o `report.json`, sem manifesto nem ZIP; ver 3.6). Só cabeçalho é válido; filtro que remove tudo gera CSV vazio com aviso.

A contagem fecha como `registros lidos = filtrados + duplicados removidos + sobreviventes`, sem contar cabeçalho ou linhas vazias ignoradas. A contagem de registros lógicos não é confundida com linhas físicas.

**Fórmulas de planilha:** preservar o texto e avisar quando houver células potencialmente interpretáveis como fórmulas, isto é, que começam com `=`, `+`, `-`, `@`, tab, CR ou LF, incluindo as formas de largura total `＝`, `＋`, `－` e `＠`. O aviso informa a contagem por coluna e deixa claro que `-` gera muitos falsos positivos (números negativos). O parâmetro opcional `formula_guard` (`none`, padrão, ou `tab_prefix`) aplica, a pedido explícito do usuário, o prefixo indicado pela OWASP; sem ele, o texto não é alterado. Não executar conteúdo no preview nem declarar o CSV seguro para qualquer planilha. Quoting CSV resolve estrutura, não neutralização universal de fórmulas. O `tab_prefix` vale para as mesmas células do aviso e põe o tab dentro de aspas, como a OWASP recomenda contra o Excel. O cabeçalho também é célula: nomes de coluna de saída com esses começos geram um aviso próprio e, com `tab_prefix`, recebem o mesmo prefixo. [D11](GoblinShiftReferencias.md#d-3), [D19](GoblinShiftReferencias.md#d-4).

<a id="s1-4"></a>
### 1.4. Downloads Detox — organizar e empacotar arquivos

**Entrada:** JPEG, PNG, CSV, TXT UTF-8 ou ZIP opaco, na quantidade de 1.7. Não abrir/descompactar ZIP. A classificação usa assinatura, extensão e validação UTF-8 incremental, e recusa executáveis, scripts, HTML e SVG reconhecíveis por esses sinais. Limite declarado: JAR, APK, DOCX e XLSX têm a mesma assinatura de um ZIP e só se distinguem abrindo o arquivo, o que o Detox não faz; eles passam como ZIP opaco. A proteção real é que nenhum arquivo é executado ou exibido como página: download com `attachment`, `nosniff` e CSP ([3.6](#s3-6)).

| Parâmetro     | Valores e default                                                                                             |
|---------------|---------------------------------------------------------------------------------------------------------------|
| `deduplicate` | Booleano; default `true`.                                                                                     |
| `group_by`    | `kind` ou `none`; default `kind`.                                                                             |
| `name_mode`   | `preserve` ou `indexed`; default `preserve`.                                                                  |
| `name_prefix` | Texto literal de até 40 caracteres, sem separadores ou controles; usado somente em `indexed`, default `file`; com `preserve`, enviar outro valor é recusado, e o default é aceito (os parâmetros resolvidos o trazem). |

**Processamento:** inspecionar → comparar conteúdo → nomear/agrupar → ZIP/manifesto; preservar os bytes originais.

Deduplicar por SHA-256 e tamanho apenas dentro do lote, preservando a primeira ocorrência na ordem de entrada. Não consultar arquivos de outras contas. Arquivos diferentes com o mesmo nome recebem sufixos estáveis; nenhum sobrescreve outro.

Grupos: `images/`, `tables/`, `text/`, `archives/`. `indexed` gera `file-001.png` pelo índice original a partir de 1; `preserve` mantém o nome seguro e resolve colisões.

**Saídas:** `downloads-detox.zip`, manifesto e relatório de todas as entradas, inclusive rejeições e duplicatas. Downloads individuais reutilizam os bytes privados; renomear não duplica objetos.

**Exemplo:** oito entradas com duas cópias exatas geram seis arquivos; nomes conflitantes são desambiguados e todas as entradas aparecem no manifesto.

TXT vazio é permitido. CSV pode ser transportado sem ser uma tabela válida para CSV Janitor. Um hash ou classificação de transporte não certifica que o conteúdo é inofensivo.

<a id="s1-5"></a>
### 1.5. Processadores complementares

PDF e mídia reutilizam upload, autenticação, Run e publicação. São receitas do produto como as demais (escopo completo decidido em 2026-10-16, ver a introdução da seção 1) e só aparecem como disponíveis no catálogo depois de implementadas.

| Processador                   | Entrada e operações                                                                                                                                                                                                                                                          | Saída e fronteiras                                                                                                                                                                                                          |
|-------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **PDF Press** — `pdf-press`   | Até 20 PDFs não criptografados, 200 páginas de entrada. `merge` concatena documentos na ordem; `split` separa páginas ou grupos de tamanho fixo; `select` recebe lista ordenada de `{upload_id, page_number}`. Páginas começam em 1 e repetições explícitas são preservadas. | PDF único ou conjunto de PDFs, mapa completo de páginas, manifesto e ZIP. Até 400 páginas de saída e 200 documentos gerados. Sem OCR, senha, edição de texto, formulários, assinaturas, anexos ou conteúdo ativo detectado. |
| **Media Mill** — `media-mill` | Até 5 vídeos, 300 s por vídeo, 1920 × 1080 e 60 fps. MP4/MOV com H.264 e AAC/PCM; WebM com VP8/VP9 e Opus/Vorbis. Extrair uma faixa de áudio e/ou gerar miniaturas.                                                                                                          | WAV PCM 16-bit ou FLAC 16-bit; JPEGs de até 640 px no maior lado; relatório, manifesto e ZIP. Sem URL remota, streaming, edição de timeline, GPU, scripts ou flags livres.                                                  |

**PDF Press:** implementar com pikepdf/qpdf. Seleção refere-se exclusivamente aos uploads da execução. Recurso excluído, página inexistente, arquivo danificado ou aviso estrutural não reconhecido falha a execução inteira; não omitir um documento e apresentar uma composição incompleta como sucesso. O resultado é um documento novo; não prometer preservar bookmarks, estrutura de acessibilidade ou assinaturas. A interface mostra a ordem por cartões numerados, sem exigir renderizador PDF. A inspeção não transforma o serviço em sanitizador universal. [D13](GoblinShiftReferencias.md#d-3).

Como isso foi implementado ([ADR 035](adr/035-pdf-press-com-pikepdf.md)): pikepdf 10.16.0 com o qpdf da wheel, só no executor. Parâmetros: `operation` (`merge`, default, `split` ou `select`), `pages_per_document` (1 a 200, só no `split`) e `select` (1 a 400 itens, só no `select`); a aceitação recusa com `422` a página de um upload fora de `input_upload_ids`. A inspeção abre sem recuperação e varre todos os objetos: aviso estrutural ou arquivo danificado é `PDF_INVALID`; senha, criptografia, JavaScript, ações fora de `GoTo`/`URI` (um `/OpenAction` que é só destino de página passa), `/AA`, formulários, assinaturas, anexos, multimídia e portfólios são `PDF_UNSUPPORTED_FEATURE`; página inexistente é `PDF_PAGE_NOT_FOUND`; mais de 200 páginas somadas ou de 100 mil objetos num documento é `INPUT_LIMIT_EXCEEDED`. A árvore de páginas é andada à mão, com tetos, antes do qpdf. Saídas na raiz de `out/`: `merged.pdf`, `selection.pdf` ou `<nome>-p001-003.pdf`, e o `page-map.json` com cada página de cada documento. O lote soma até 256 MiB.

**Media Mill:** implementar com FFmpeg/ffprobe. Os parâmetros expõem `extract_audio`, `audio_format`, `audio_stream_index` e `thumbnail_count` de 0, 1 ou 3; ao menos uma saída deve ser solicitada. Ausência de índice seleciona a primeira faixa de áudio, registrando o índice efetivo. Preservar taxa e canais até 48 kHz e estéreo; rejeitar fontes acima disso, sem downmix/resampling implícito. A saída de 16 bits é uma conversão declarada; FLAC não recupera qualidade perdida no arquivo de origem. O FFmpeg nunca abre outro arquivo além da entrada, nem por conteúdo disfarçado (um `.mp4` que é playlist HLS, o demuxer `concat`); como isso é garantido está em [2.3](#s2-3).

Uma miniatura utiliza o ponto de 10% da duração; três utilizam 10%, 50% e 90%. Selecionar o primeiro frame decodificado com timestamp igual ou posterior ao alvo; se não existir, usar o último anterior e registrar aviso. Guardar instante solicitado e efetivo. O perfil aceita pixels quadrados e orientação de 0/90/180/270 graus. Vídeo sem áudio é válido quando foram solicitadas apenas miniaturas; caso contrário, rejeitar o vídeo inteiro, preservando o contrato das saídas solicitadas. Probe e conversão ficam na mesma fronteira de isolamento. [D14](GoblinShiftReferencias.md#d-3).

<a id="s1-6"></a>
### 1.6. Contrato HTTP

**Base:** `/api/v1`. JSON para comandos e metadados; corpo binário no envio de arquivo. Modelos distintos de entrada e saída e `operationId` estável. **Schema por receita, uma fonte só:** no OpenAPI 3.1, o corpo de `POST /runs` e o de presets são `oneOf` com uma variante por `(recipe_id, recipe_version)`, discriminadas por `recipe_id`, com `unevaluatedProperties: false`; o catálogo não copia schemas, só os referencia por `(recipe_id, versão)`, e `GET /catalog/{recipe_id}/versions/{version}` devolve schema, defaults e limites de uma versão. A lista `input_upload_ids` é ordenada e não aceita IDs repetidos; repetição de páginas PDF é expressa na seleção de páginas, não duplicando a entrada. A documentação interativa fica em `/docs`; o contrato gerado, em `/openapi.json`. [D02](GoblinShiftReferencias.md#d-1), [D04](GoblinShiftReferencias.md#d-1).

| Método e rota                                                | Resultado                                                                                                                                                                                                    | Permissão por chave             |
|--------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------|
| `GET /catalog`                                               | Receitas disponíveis, versões, schemas, defaults e limites.                                                                                                                                                  | Qualquer chave válida.          |
| `GET /me`                                                    | Identidade atual, limites, consumo das cotas e aceite dos termos.                                                                                                                                             | Qualquer chave válida.          |
| `DELETE /me`                                                 | `202`: exclusão da conta e de todos os seus dados ([1.8](#s1-8)).                                                                                                                                            | Somente sessão web autenticada. |
| `POST /me/terms:accept`                                      | `200`: aceite da versão vigente dos termos, idempotente ([1.8](#s1-8); [ADR 029](adr/029-aceite-versionado-dos-termos.md)).                                                                                  | Somente sessão web autenticada. |
| `POST /uploads`                                              | `201`: lote de 1 a 100 intenções (nome e tamanho esperado), na ordem enviada, cada uma com `id`, `expires_at` e `content: {method, url, headers}` pronto para o envio.                                       | `files:write`                   |
| `PUT /uploads/{id}/content`                                  | `200`: arquivo persistido, tamanho real, SHA-256 e estado.                                                                                                                                                   | `files:write`                   |
| `GET /uploads/{id}`                                          | Estado, metadados e disponibilidade do próprio upload.                                                                                                                                                       | `files:read`                    |
| `DELETE /uploads/{id}`                                       | `202`: exclusão aceita; novos usos e downloads são bloqueados na hora e a remoção física espera os usos ativos terminarem. Repetir a chamada também responde `202`.                                          | `files:write`                   |
| `GET /presets` / `GET /presets/{id}`                         | Configurações privadas, com revisão.                                                                                                                                                                         | `presets:read`                  |
| `POST /presets`                                              | `201`: preset validado.                                                                                                                                                                                      | `presets:write`                 |
| `PUT /presets/{id}`                                          | `200`: atualização integral com `If-Match`.                                                                                                                                                                  | `presets:write`                 |
| `DELETE /presets/{id}`                                       | `204`: exclusão com `If-Match`; execuções anteriores não mudam.                                                                                                                                              | `presets:write`                 |
| `POST /presets/{id}/runs`                                    | `202`: nova execução com receita, versão e parâmetros do preset, que fica registrado na Run; requer `Idempotency-Key`.                                                                                       | `runs:write` e `presets:read`   |
| `POST /runs`                                                 | `202`: execução persistida; requer `Idempotency-Key`. Com `?validate_only=true`, `200` com os parâmetros resolvidos (defaults aplicados) ou `422`, sem criar Run, sem consumir a chave e sem exigir uploads. | `runs:write`                    |
| `GET /runs` / `GET /runs/{id}`                               | Histórico paginado ou estado detalhado.                                                                                                                                                                      | `runs:read`                     |
| `POST /runs/{id}/cancel`                                     | `202` enquanto encerra; `200` se já terminal, devolvendo o estado real.                                                                                                                                      | `runs:write`                    |
| `GET /runs/{id}/results`                                     | `200`: `published` (se a publicação já aconteceu), `expires_at` e as referências dos resultados. `published: false` distingue "ainda não publicado" de "publicado sem saídas".                               | `runs:read`                     |
| `DELETE /runs/{id}/artifacts`                                | `202`: excluir as saídas de uma execução terminal antes das 48 h, com a mesma exclusão adiada de 3.6; não exclui originais.                                                                                  | `files:write`                   |
| `GET /artifacts/{id}`                                        | Metadados, papel, hash, tamanho e expiração.                                                                                                                                                                 | `files:read`                    |
| `GET /artifacts/{id}/download`                               | Arquivo privado por streaming autenticado.                                                                                                                                                                   | `files:read`                    |
| `GET /artifacts/{id}/preview`                                | Apenas imagem derivada ou amostra tabular escapada, quando disponível.                                                                                                                                       | `files:read`                    |
| `GET /api-keys` / `POST /api-keys` / `DELETE /api-keys/{id}` | Listar metadados, emitir segredo uma vez ou revogar.                                                                                                                                                         | Somente sessão web autenticada. |

Login fora da base: `GET /auth/login/{provider}`, `GET /auth/callback/{provider}` (com `provider` igual a `google` ou `github`) e `POST /auth/logout`. `POST /auth/logout-all` revoga todas as sessões e chaves da conta, para quem suspeita de sequestro. Sessão usa permissões da conta; chave também exige escopos. Nenhuma acessa recursos de outra conta.

**Upload em lote:** o cliente usa `content.method`, `content.url` e `content.headers` exatamente como recebidos, o que permite trocar no futuro por URLs pré-assinadas sem quebrar clientes. Um lote de 100 arquivos custa uma chamada `POST /uploads` e 100 `PUT` de conteúdo, não 200 chamadas de intenção. Os `PUT` são controlados pelo limite de transferências simultâneas (1.7), não pela cota de requisições por minuto, para que o maior lote prometido caiba nos próprios limites.

**Acompanhamento:** `GET /runs/{id}` de uma execução não terminal devolve `Retry-After: 2`, que é o intervalo de polling esperado. A resposta informa `queue_ahead` (quantas execuções aceitas antes desta ainda não começaram), um dado real e não uma estimativa de tempo.

**Presets:** `name` (1 a 100 caracteres, único na conta), `recipe_id`, `recipe_version`, `parameters`, `revision`. ETag/If-Match controlam edição e exclusão: ausente → `428`, obsoleto → `412`. O `ETag` é a revisão entre aspas (`"3"`), e `If-Match: *` não é aceito, porque valeria para qualquer revisão. Repetir preset (`POST /presets/{id}/runs`) requer novos uploads; editar ou excluir o preset não altera execuções.

**Idempotência:** chave por conta e operação durante 24 h, seguindo o draft IETF do header `Idempotency-Key` (é draft, não norma; [D04](GoblinShiftReferencias.md#d-1)). Formato fixo: 16 a 64 caracteres `[A-Za-z0-9_-]`, como um UUID. O hash do comando aplica os defaults da versão da receita, ordena chaves e preserva a ordem dos arrays; ele só é estável porque defaults nunca mudam dentro de uma `recipe_version` (ver evolução abaixo). Informar `idempotency_expires_at`; após esse prazo, a chave pode criar nova Run.

| Caso                                                               | Resposta                                                                                          |
|--------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| Chave ausente ou fora do formato                                   | `400` com `IDEMPOTENCY_KEY_INVALID`                                                               |
| Mesma chave, mesmo comando                                         | `202` com o mesmo `Location`, o estado atual da Run e `Idempotency-Replayed: true`; nada é criado |
| Mesma chave, comando diferente                                     | `422` com `IDEMPOTENCY_KEY_REUSED`                                                                |
| Mesma chave enquanto a requisição original ainda está em andamento | `409` com `IDEMPOTENCY_IN_PROGRESS` e `Retry-After`                                               |

**Evolução de receitas e da API:**
- Dentro de uma `recipe_version`, schema, defaults, enums e semântica nunca mudam. Qualquer mudança de comportamento cria uma versão nova. Os limites operacionais de 1.7 (bytes, contagens) não fazem parte da versão: são fixados na Run no momento da aceitação.
- O catálogo marca cada versão como `current` ou `deprecated`. No máximo duas versões convivem (a atual e a anterior), e uma versão `deprecated` continua aceita por pelo menos 90 dias, com o header `Sunset` (RFC 8594) nas respostas que a usam.
- Versão retirada: `POST /runs` e presets respondem `422` com `RECIPE_VERSION_RETIRED`; o histórico continua legível.
- Em `/api/v1` só entram mudanças aditivas. Clientes devem tolerar valores novos em enums de saída (`status`, `phase`, disponibilidade, `recipe_id`), porque acrescentar valor a um enum de saída quebra quem não tolera. Mudança incompatível vai para `/api/v2`.

**Listagem:** envelope `{"data": [...], "next_cursor": "…" | null}`, mais recentes primeiro (`created_at` e `id` decrescentes), 25 itens por padrão e `limit` de 1 a 100 (fora disso, `422`). Filtros: `recipe_id`, `status` e período por `created_from` (inclusivo) e `created_to` (exclusivo). O cursor é opaco, carrega filtros e direção e vale por 24 horas; usado com filtros diferentes, responde `400`. O fim da lista é `next_cursor: null`, não uma página vazia. Datas RFC 3339 UTC; IDs são strings.

**Erros:** `application/problem+json`, conforme RFC 9457, com `type`, `title`, `status`, `detail`, `instance`, `code`, `request_id` e, quando pertinente, `errors` por campo. Não devolver stack traces, queries, segredos ou dados de outra conta. Toda resposta de erro é problem+json, inclusive validação, rota inexistente e método não permitido, e é declarada no OpenAPI com o schema `ProblemDetails` ([2.3](#s2-3)). Os `code`s formam um catálogo público, cada um com URI de `type` estável. [D04](GoblinShiftReferencias.md#d-1).

| Status                | Uso                                                                                                                                                                                                                                                                  |
|-----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `400`                 | JSON malformado, chaves duplicadas ou credenciais simultâneas ambíguas.                                                                                                                                                                                              |
| `401` / `403`         | Credencial ausente/inválida, autenticação recente exigida (`REAUTH_REQUIRED`), escopo insuficiente ou verificação de CSRF recusada (`CSRF_FAILED`).                                                                                                                  |
| `404`                 | Recurso **da URL** inexistente ou pertencente a outra conta. IDs citados no corpo nunca geram `404`: um `upload_id` inexistente, de outra conta ou expirado em `POST /runs` responde `422` com um único código, `INPUT_NOT_AVAILABLE`, para não permitir enumeração. |
| `408`                 | Transferência de conteúdo interrompida por tempo (10 minutos no total ou 30 s sem progresso).                                                                                                                                                                        |
| `409`                 | Requisição idempotente ainda em andamento, estado incompatível ou já existe uma execução não terminal na conta (`ACTIVE_RUN_LIMIT`).                                                                                                                                 |
| `410`                 | Conteúdo expirado/excluído conhecido do proprietário, ou cujo arquivo se perdeu (`missing`, [1.7](#s1-7)).                                                                                                                                                          |
| `412` / `428`         | Revisão de preset desatualizada (`412`, RFC 9110) ou precondição ausente (`428`, RFC 6585).                                                                                                                                                                          |
| `413` / `415` / `422` | Corpo acima do limite, mídia HTTP incompatível ou comando semanticamente inválido.                                                                                                                                                                                   |
| `429`                 | Limite técnico da conta, cota diária ou taxa excedida; incluir `Retry-After` quando aplicável.                                                                                                                                                                       |
| `503`                 | Fila global cheia, capacidade global ou dependência necessária indisponível; incluir `Retry-After`.                                                                                                                                                                  |
| `500`                 | Falha inesperada; corpo genérico com `request_id`, sem detalhe interno.                                                                                                                                                                                              |

Após `202`, conteúdo ainda pode falhar na execução; não se altera a resposta HTTP retroativamente.

**Catálogo de códigos.** Uma tabela só para erros HTTP, rejeições por item e falhas de execução. Cada `code` tem URI de `type` estável no problem+json.

| Código                      | Classe         | Retry automático     | Resultado                                                   | HTTP  |
|-----------------------------|----------------|----------------------|-------------------------------------------------------------|-------|
| `JSON_INVALID`              | Requisição     | —                    | Recusado (malformado, chave duplicada, `NaN`, profundidade) | `400` |
| `IDEMPOTENCY_KEY_INVALID`   | Requisição     | —                    | Recusado                                                    | `400` |
| `BAD_REQUEST`               | Requisição     | —                    | Recusado (genérico: nada do catálogo se aplica)             | `400` |
| `AUTHENTICATION_REQUIRED`   | Requisição     | —                    | Credencial ausente ou inválida                              | `401` |
| `REAUTH_REQUIRED`           | Requisição     | —                    | Refazer login                                               | `401` |
| `INSUFFICIENT_SCOPE`        | Requisição     | —                    | Recusado (escopo da chave)                                  | `403` |
| `FORBIDDEN`                 | Requisição     | —                    | Recusado (genérico: acesso negado sem motivo do catálogo)   | `403` |
| `CSRF_FAILED`               | Requisição     | —                    | Recusado e auditado                                         | `403` |
| `TERMS_NOT_ACCEPTED`        | Requisição     | —                    | Aceitar a versão vigente dos termos                         | `403` |
| `NOT_FOUND`                 | Requisição     | —                    | Recusado (inexistente ou de outra conta)                    | `404` |
| `METHOD_NOT_ALLOWED`        | Requisição     | —                    | Recusado, com `Allow`                                       | `405` |
| `REQUEST_TIMEOUT`           | Requisição     | —                    | Transferência interrompida por tempo                        | `408` |
| `STATE_CONFLICT`            | Requisição     | —                    | Recusado (estado incompatível)                              | `409` |
| `IDEMPOTENCY_IN_PROGRESS`   | Requisição     | —                    | Tentar de novo após `Retry-After`                           | `409` |
| `ACTIVE_RUN_LIMIT`          | Política       | —                    | Recusado                                                    | `409` |
| `GONE`                      | Requisição     | —                    | Conteúdo expirado, excluído ou perdido (`missing`)          | `410` |
| `PRECONDITION_FAILED`       | Requisição     | —                    | Revisão desatualizada; leia de novo                         | `412` |
| `BODY_TOO_LARGE`            | Requisição     | —                    | Recusado                                                    | `413` |
| `UNSUPPORTED_MEDIA_TYPE`    | Requisição     | —                    | Recusado                                                    | `415` |
| `VALIDATION_FAILED`         | Requisição     | —                    | Recusado, com `errors` por campo                            | `422` |
| `IDEMPOTENCY_KEY_REUSED`    | Requisição     | —                    | Recusado                                                    | `422` |
| `INPUT_NOT_AVAILABLE`       | Requisição     | —                    | Recusado                                                    | `422` |
| `RECIPE_VERSION_RETIRED`    | Requisição     | —                    | Recusado                                                    | `422` |
| `PRECONDITION_REQUIRED`     | Requisição     | —                    | Envie `If-Match` com o `ETag` lido                          | `428` |
| `QUOTA_EXCEEDED`            | Política       | —                    | Recusado, com `Retry-After`                                 | `429` |
| `RATE_LIMITED`              | Política       | —                    | Recusado, com `Retry-After`                                 | `429` |
| `INTERNAL_ERROR`            | Servidor       | —                    | Falha inesperada, sem detalhe interno                       | `500` |
| `QUEUE_FULL`                | Política       | —                    | Recusado, com `Retry-After`                                 | `503` |
| `SERVICE_UNAVAILABLE`       | Servidor       | —                    | Dependência indisponível (genérico), com `Retry-After`      | `503` |
| `UNSUPPORTED_FORMAT`        | Item           | Não                  | Arquivo rejeitado; a execução continua                      | —     |
| `UNSUPPORTED_BIT_DEPTH`     | Item           | Não                  | Arquivo rejeitado; a execução continua                      | —     |
| `UNSUPPORTED_COLOR_PROFILE` | Item           | Não                  | Arquivo rejeitado; a execução continua                      | —     |
| `IMAGE_TOO_LARGE`           | Item           | Não                  | Arquivo rejeitado; a execução continua                      | —     |
| `CSV_STRUCTURE_INVALID`     | Item           | Não                  | Tabela rejeitada; a execução continua                       | —     |
| `FIELD_LIMIT_EXCEEDED`      | Item           | Não                  | Tabela rejeitada; a execução continua                       | —     |
| `NO_VALID_OUTPUTS`          | Determinístico | Não                  | `failed`                                                    | —     |
| `INPUT_LIMIT_EXCEEDED`      | Determinístico | Não                  | `failed` (páginas ou objetos de entrada acima do teto, 1.5) | —     |
| `PDF_INVALID`               | Determinístico | Não                  | `failed` (PDF danificado ou com aviso estrutural, 1.5)      | —     |
| `PDF_UNSUPPORTED_FEATURE`   | Determinístico | Não                  | `failed` (senha, formulário, assinatura, anexo, conteúdo ativo, 1.5) | — |
| `PDF_PAGE_NOT_FOUND`        | Determinístico | Não                  | `failed` (página selecionada inexistente, 1.5)              | —     |
| `OUTPUT_LIMIT_EXCEEDED`     | Determinístico | Não                  | `failed`                                                    | —     |
| `REPORT_LIMIT_EXCEEDED`     | Determinístico | Não                  | `failed`                                                    | —     |
| `RESOURCE_LIMIT_EXCEEDED`   | Determinístico | Não                  | `failed` (memória, disco, processos ou 10 min da tentativa) | —     |
| `EXECUTOR_UNAVAILABLE`      | Transitório    | Sim, dentro do prazo | Nova tentativa ou, sem tentativas, `failed`                 | —     |
| `QUEUE_EXPIRED`             | Parada         | Não                  | `failed`                                                    | —     |
| `DEADLINE_EXCEEDED`         | Parada         | Não                  | `failed`                                                    | —     |
| `INFRA_POISON`              | Parada         | Não                  | `failed`                                                    | —     |
| `CANCELED`                  | Parada         | Não                  | `canceled`                                                  | —     |

Os códigos genéricos (`BAD_REQUEST`, `AUTHENTICATION_REQUIRED`, `INSUFFICIENT_SCOPE`, `FORBIDDEN`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `REQUEST_TIMEOUT`, `STATE_CONFLICT`, `GONE`, `PRECONDITION_FAILED`, `UNSUPPORTED_MEDIA_TYPE`, `PRECONDITION_REQUIRED`, `INTERNAL_ERROR`, `SERVICE_UNAVAILABLE`) cobrem os status da tabela acima que não tinham código próprio. O status de cada `code` sai do catálogo em `entrypoints/api/problems.py`, para que um código nunca apareça com dois status; um erro HTTP do framework com status fora do catálogo sai como `BAD_REQUEST` (4xx) ou `INTERNAL_ERROR` (5xx), com o status do catálogo. A URI de `type` é `https://goblinshift.dev/problems/<code-em-kebab>` (por exemplo, `.../problems/body-too-large`), gerada por `ProblemCode.type_uri` no mesmo arquivo; é um identificador estável, não precisa ser navegável.

No código das receitas, a classe sai do tipo levantado: `ItemRejected(code)` vira rejeição por item no relatório, `RecipeFailure(code)` é falha determinística, e qualquer outra exceção é tratada como infraestrutura (transitória). Só dois lugares traduzem exceções: o handler do FastAPI e o laço do worker.

**Exemplo de execução:**

```http
POST /api/v1/runs
Authorization: Bearer <API_KEY>
Content-Type: application/json
Idempotency-Key: 3f0c1b6e-6a2d-4c55-9a7e-2b1f8d4c9e10
```

```json
{
  "recipe_id": "image-forge",
  "recipe_version": 1,
  "input_upload_ids": [
    "4eb3e442-3f30-4d83-8d34-cb18a99a46cb",
    "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2"
  ],
  "parameters": {
    "max_width": 1600,
    "max_height": 1600,
    "output_format": "jpeg",
    "jpeg_quality": 85,
    "jpeg_background": "#FFFFFF",
    "make_thumbnails": true
  }
}
```

Resposta `202`, com `Location: /api/v1/runs/89b4bc22-ad2e-43d6-8611-911822c85e36`:

```json
{
  "id": "89b4bc22-ad2e-43d6-8611-911822c85e36",
  "recipe_id": "image-forge",
  "recipe_version": 1,
  "status": "queued",
  "attempt": 0,
  "progress": {"phase": "queued", "processed": 0, "total": 2},
  "queue_ahead": 0,
  "created_at": "2026-10-12T15:00:00Z",
  "queue_expires_at": "2026-10-12T17:00:00Z",
  "deadline_at": null,
  "idempotency_expires_at": "2026-10-13T15:00:00Z"
}
```

IDs e horários são exemplos. `queue_expires_at` é o prazo para a execução começar; `deadline_at` só é preenchido quando a primeira tentativa começa (1.7). `progress.processed` inclui entradas avaliadas na tentativa atual, inclusive rejeições, não sucessos. `attempt`, `phase` e `progress` são **informativos**: servem para a interface explicar o andamento (por exemplo, "tentativa 2 de 2" quando o progresso volta a zero), mas a política de retentativa e as fases podem mudar sem nova versão da API, e clientes só devem basear decisões em `status`. `phase` só existe em `running` (`inspecting`, `processing`, `packaging`, `finalizing`, `retry_wait`) e é `null` nos demais estados; ver a tabela de transições em [3.5](#s3-5). Execução terminal com falha traz `error: {code, message}`, com `code` do catálogo abaixo.

<a id="s1-7"></a>
### 1.7. Limites e disponibilidade dos arquivos

Limites iniciais configuráveis e publicados no catálogo, não benchmarks. Aplicar todos os tetos e prevalecer o menor.

| Recurso        | Limite inicial                                                                                                                                                                                                                                                             |
|----------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Comando JSON   | 64 KiB; profundidade máxima 16. Sem campos desconhecidos, chaves duplicadas, `NaN` ou infinito.                                                                                                                                                                            |
| Arquivo comum  | 32 MiB; vídeo, quando disponível, até 256 MiB.                                                                                                                                                                                                                             |
| Lote           | 100 arquivos e 512 MiB no total; limites específicos da receita prevalecem.                                                                                                                                                                                                |
| Imagem         | 24 megapixels e 8192 px por eixo, além do limite de bytes.                                                                                                                                                                                                                 |
| CSV            | 5 tabelas de até 8 MiB; 20.000 registros e 100 colunas por tabela; 64 KiB por campo e 1 MiB por registro lógico.                                                                                                                                                           |
| Saídas         | 480 MiB de conteúdo, 8 MiB para cada relatório/manifesto e ZIP até 512 MiB; máximo de 220 arquivos de conteúdo/preview.                                                                                                                                                    |
| Processamento  | Uma tentativa física por vez inicialmente; até 10 execuções aguardando globalmente e uma execução não terminal por conta. Fila cheia → `503` com `Retry-After`.                                                                                                            |
| Executor       | 1 vCPU, 2 GiB RAM, 64 processos e 6 GiB de área de trabalho (temporários + saídas) em filesystem de tamanho fixo ([3.6](#s3-6)). Com 1 vCPU no KVM 2 ([3.8](#s3-8)), o teto de 10 minutos por tentativa é confirmado com medição na fatia 4. |
| Fila           | A execução precisa começar em até 2 horas após a aceitação (`queue_expires_at`); se não começar, termina `failed` com `QUEUE_EXPIRED` e libera as reservas.                                                                                                                |
| Duração        | 10 minutos por tentativa e 30 minutos por execução, contados a partir do início da primeira tentativa (`deadline_at`), incluindo retentativa, coleta e publicação.                                                                                                         |
| Cotas diárias  | Por conta: 30 execuções e 2 GiB de entrada processada a cada 24 horas corridas.                                                                                                                                                                                            |
| Transferências | 2 uploads e 2 downloads simultâneos por conta; 4 de cada no ambiente. Até 10 minutos por transferência e 30 s sem progresso. O limite vale entre réplicas: a tabela `transfer_slot` guarda as vagas, e cada reserva é serializada por direção com advisory lock de transação. A vaga vence em 11 minutos (10 de transferência e 1 de folga), para não travar a conta se a réplica cair. Sem vaga: `429` `RATE_LIMITED` com `Retry-After`. |
| Armazenamento  | 4 GiB por conta e 20 GiB no ambiente, incluindo reservas e objetos pendentes de exclusão.                                                                                                                                                                                  |
| Requisições    | Por classe de rota e por conta: leituras e polling, 120 por minuto; comandos, 60 por minuto; criação de execução, 10 por minuto. O envio e o download de conteúdo são limitados pelas transferências simultâneas, não por minuto. Rotas `/auth/*`: limite por IP no Nginx. |
| Configurações  | Até 100 presets e 5 chaves de API por conta.                                                                                                                                                                                                                               |

**Por que o prazo foi dividido:** com uma tentativa física por vez, cada execução pode ocupar cerca de 21 minutos (2 × 10 min, espera de retentativa e parada). Um prazo único de 30 minutos contado desde a aceitação faria a 3ª ou 4ª execução da fila vencer antes de começar, depois de a API ter respondido `202`. Agora a aceitação recusa na hora quando a fila global está cheia, a espera tem prazo próprio e explícito, e o prazo de execução só corre quando o trabalho começa.

Reservar capacidade com atualização atômica no banco. Considerar arquivos recebidos, candidatos, conteúdo final, pacote e temporários; não presumir que compressão diminuirá bytes. Referências repetidas não multiplicam armazenamento lógico; cópias físicas continuam ocupando espaço. Limites de RAM e CPU não substituem limite de disco.

**Retenção.** O serviço não mantém arquivo permanente, e nada acumula sem um mecanismo que recicle (*Release It!*, Steady State, [B21](GoblinShiftReferencias.md#l-6)).

| Dado                                           | Prazo                                             | Como expira                                       |
|------------------------------------------------|---------------------------------------------------|---------------------------------------------------|
| Upload não utilizado                           | 24 horas                                          | Objeto removido; linha mantida com `deleted_at` (a FK de `RunInput` preserva o histórico) |
| Upload incompleto                              | 1 hora sem atividade                              | Objeto e linha removidos                          |
| Entradas usadas, saídas, manifesto e relatório | 48 horas após o término                           | Objetos removidos; disponibilidade vira `expired` |
| Upload reutilizado                             | Enquanto houver execução ativa ou retenção válida | Segue o vínculo mais longo                        |
| Nomes, `run.parameters` e detalhes por item    | 30 dias ou exclusão anterior                      | Colunas anuladas; a linha da Run permanece        |
| Histórico resumido (Run e Attempt)             | 90 dias                                           | Linhas removidas                                  |
| `IdempotencyRecord`                            | 24 horas                                          | Linha removida                                    |
| Estado transitório de login                    | Poucos minutos                                    | Entrada do cache removida                         |
| Sessões expiradas ou revogadas                 | 7 dias após o fim                                 | Linha removida                                    |
| `AuditEvent`                                   | 90 dias                                           | Linha removida                                    |
| Containers e área de trabalho do executor      | Até a coleta ser persistida                       | Removidos pelo `executor-ctl`                     |
| Telemetria (eventos amplos e traces)           | 30 dias                                           | Expiração no backend de telemetria                |
| Logs da aplicação e dos containers             | ≥ 14 dias no volume previsto; teto de 50 MiB      | Rotação por tamanho, por serviço, no Compose      |
| Backups do PostgreSQL                          | 30 dias                                           | Expiração no armazenamento externo ([3.8](#s3-8)) |

Os jobs de expiração rodam no processo `maintenance`, em lotes, com índices parciais sobre as linhas ainda não expiradas.

**Logs dos containers.** O Docker só gira log por tamanho, não por idade, e a regra verificável é o teto: cada serviço do Compose usa o driver `local` com 5 arquivos de 10 MiB, o que cobre 14 dias até cerca de 3,5 MiB de log por dia e serviço (o Nginx grava perto de 250 bytes por requisição, ou uns 15 mil acessos por dia). Com pouco tráfego, uma linha pode durar mais de 14 dias; por isso os logs seguem a mesma minimização dos eventos (3.7) e somem quando o container é recriado, o que acontece a cada deploy da imagem. A rotação fica no Compose, e nunca no `/etc/docker/daemon.json`, que vale para todos os projetos da VPS compartilhada ([3.8](#s3-8)). Os containers do executor, criados fora do Compose, declaram o limite no perfil fixo do `executor-ctl`: um arquivo de 1 MiB, porque o executor só escreve ali o diagnóstico de falha. `tests/test_container_logs.py` confere que todo serviço de produção tem a rotação e que a soma cabe em 512 MiB da raiz. O teste de aceitação confere o limite do executor no `HostConfig.LogConfig`.

A disponibilidade é independente do estado histórico: uma execução pode continuar `succeeded` com resultados `expired`. Exclusão de saída não apaga o original; exclusão de original afeta sua disponibilidade em históricos anteriores, mas não muda os resultados já produzidos. Objetos ausentes inesperadamente são marcados `missing`, não substituídos por arquivos vazios. Só a varredura da manutenção marca, nunca uma requisição: o download que encontra o arquivo ausente responde `410` sem gravar nada. A varredura confere em rodízio um lote de objetos `committed` por ciclo e só marca com a sentinela de montagem presente na raiz do storage (gravada pelo deploy depois de conferir a montagem) e com no máximo metade do lote ausente. Acima disso, num lote pequeno inteiro ausente ou com erro de disco junto com ausentes, para e alerta, porque é sinal de storage errado e não de arquivo perdido. Erro de disco que não seja ausência deixa o objeto como está, e a varredura segue. Só `committed` vira `missing`, por `UPDATE` condicional. `missing` é final: sem backup de arquivos, nada devolve o arquivo, então a reserva de armazenamento do objeto é liberada na mesma transação. A disponibilidade publicada passa a `missing`, salvo se o recurso já estiver excluído ou expirado, causas que prevalecem.

<a id="s1-8"></a>
### 1.8. Serviço aberto: contas, abuso e privacidade

**Contas:** qualquer conta Google (OpenID Connect) ou GitHub (OAuth 2.0) cria um usuário no primeiro login, sem convite. A identidade é `(issuer, subject)`: no Google, `https://accounts.google.com` + `sub`; no GitHub, `https://github.com` + o `id` numérico de `GET /user`, nunca o `login`, que pode mudar. A mesma pessoa entrando por Google e por GitHub tem duas contas; não há vínculo automático por e-mail. Detalhes do fluxo em [3.6](#s3-6).

**Estado da conta:** `active` ou `blocked`. Bloquear é uma operação de administrador, feita por CLI no servidor (sem painel web na v1): revoga sessões e chaves, cancela a execução não terminal e impede novos logins da mesma identidade.

**Abuso e custo:** a hospedagem tem custo fixo e capacidade finita, então nenhuma conta pode monopolizar o serviço.
- Uma execução não terminal por conta, cotas diárias, limites por classe de rota e fila global limitada ([1.7](#s1-7)).
- Recusar cedo: fila cheia ou cota esgotada respondem na aceitação, nunca depois de horas de espera.
- Consumo por conta registrado (execuções, bytes, tempo de executor) e visível em `GET /me`; alerta para o administrador quando uma conta ou o ambiente se aproximam dos tetos.
- Conteúdo é privado e expira em 48 horas; não há links públicos de compartilhamento, o que reduz o uso do serviço para hospedar conteúdo de terceiros.
- Os termos de uso proíbem conteúdo ilegal e uso automatizado abusivo, e publicam um canal de contato para denúncias.

**Privacidade (LGPD):**
- *Minimização:* pedir o mínimo aos provedores (`openid` no Google; nenhum escopo no GitHub) e guardar só `issuer`, `subject` e um nome de exibição quando vier sem escopo extra (o `login` do GitHub). E-mail não é coletado.
- *Base legal e transparência:* execução do contrato de uso. Aviso de privacidade e termos publicados e aceitos no primeiro login, com versão e data registradas.
- *Acesso:* `GET /me`, histórico e presets mostram tudo o que a conta guarda.
- *Exclusão:* `DELETE /me` revoga sessões e chaves, cancela a execução não terminal e remove uploads, artefatos, presets e histórico em até 24 horas. Cópias em backup expiram com o ciclo de backups ([3.8](#s3-8)), e o aviso de privacidade diz isso.
- *Localização:* preferir o datacenter da Hostinger no Brasil; se outro for usado, o aviso de privacidade declara a transferência internacional.
- Logs e métricas seguem a regra de 3.7: sem conteúdo, nomes de arquivo ou tokens. Eventos de segurança guardam IP truncado por 90 dias, por segurança do próprio serviço ([3.6](#s3-6)). A telemetria fica na própria VPS, sem envio a terceiros ([3.7](#s3-7)).
- Os textos de termos e privacidade devem passar por revisão jurídica antes da abertura pública.

**Licenças:** o repositório é público e não contém segredos. O Media Mill usa um build do FFmpeg sem `--enable-gpl` e sem `--enable-nonfree` (LGPL), com os avisos de licença publicados junto com a imagem do executor. [D14](GoblinShiftReferencias.md#d-3).

<a id="s1-9"></a>
### 1.9. Glossário

| Termo                 | Significado                                                                                                               |
|-----------------------|---------------------------------------------------------------------------------------------------------------------------|
| Receita               | Conjunto fixo de operações (Image Forge, CSV Janitor, Downloads Detox…), identificado por `recipe_id` e `recipe_version`. |
| Preset                | Parâmetros salvos de uma receita, sem arquivos.                                                                           |
| Execução (`Run`)      | Um pedido aceito: entradas, parâmetros e limites fixados, estado e resultados. Também é o item da fila.                   |
| Tentativa (`Attempt`) | Uma execução física do lote dentro de um container; uma Run tem no máximo duas.                                           |
| Claim                 | A transação em que o worker reivindica uma Run elegível e cria a tentativa.                                               |
| Geração               | Contador da Run incrementado a cada claim; token de fencing que invalida escritas de tentativas antigas.                  |
| Lease                 | Prazo, renovado pelo worker, durante o qual a tentativa é considerada viva.                                               |
| Coordenador           | O papel do worker durante uma tentativa: pede o container, acompanha o progresso, coleta e publica.                       |
| Executor              | O container descartável, sob gVisor, que roda a receita sem rede nem credenciais.                                         |
| `executor-ctl`        | Serviço do host, único com acesso ao Docker, com quatro operações (`start`, `status`, `stop`, `remove`).                  |
| Candidato             | Saída gravada pela tentativa antes da publicação; privada até o commit.                                                   |
| Publicação            | A transação que torna as saídas visíveis e grava o estado terminal, condicionada à geração, ao prazo e ao cancelamento.   |
| Objeto armazenado     | `StoredObject`: um arquivo físico no storage, referenciado por uploads e artefatos.                                       |
| Uso ativo             | `ObjectUse`: uma execução ou um download em andamento que impede a remoção física de um objeto.                           |
| Digest                | Hash da imagem do executor que implementa a versão da receita, fixado na Run para reprodutibilidade.                      |
| Manutenção            | O processo `maintenance`: reconciliação, expiração e limpeza, sempre em lotes.                                            |
| Razão de parada       | O primeiro motivo persistido para encerrar uma execução (cancelamento, prazo, falha); define `canceled` ou `failed`.      |

[↑ Sumário](#sumario)

---

<a id="stack"></a>
## 2. Stack

<a id="s2-1"></a>
### 2.1. Componentes escolhidos

| Responsabilidade         | Tecnologia                                                          | Aplicação                                                                                                                                                                                    |
|--------------------------|---------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Backend                  | **Python 3.14 + FastAPI + Uvicorn**                                 | API REST, dependências HTTP, validação de fronteira e documentação OpenAPI. [D01](GoblinShiftReferencias.md#d-1), [D02](GoblinShiftReferencias.md#d-1)                                       |
| Contratos e configuração | **Pydantic 2 + pydantic-settings**                                  | Requests, responses, parâmetros por receita, serialização e settings. [D03](GoblinShiftReferencias.md#d-1)                                                                                   |
| Dados                    | **PostgreSQL + SQLAlchemy 2 + psycopg 3**                           | Identidade, execuções, presets, idempotência, reservas e transações. [D05](GoblinShiftReferencias.md#d-2), [D06](GoblinShiftReferencias.md#d-2)                                              |
| Schema do banco          | **Alembic**                                                         | Criação e versionamento do schema. [D07](GoblinShiftReferencias.md#d-2)                                                                                                                      |
| Trabalho assíncrono      | **Fila no PostgreSQL** (`FOR UPDATE SKIP LOCKED` + `LISTEN/NOTIFY`) | A própria tabela de execuções é a fila: aceitar e enfileirar são a mesma transação, sem broker nem dupla escrita. [D08](GoblinShiftReferencias.md#d-2), [D09](GoblinShiftReferencias.md#d-2) |
| Imagens                  | **Pillow**                                                          | Decode, orientação, resize, conversão e miniaturas. [D12](GoblinShiftReferencias.md#d-3)                                                                                                     |
| CSV e arquivos           | **Biblioteca padrão Python**                                        | `csv`, `hashlib`, `zipfile`, `pathlib`, `unicodedata` e streaming. Sem pandas/Polars para este contrato. [D11](GoblinShiftReferencias.md#d-3)                                                |
| PDF Press                | **pikepdf / qpdf**                                                  | Composição e seleção estrutural de páginas. [D13](GoblinShiftReferencias.md#d-3)                                                                                                             |
| Media Mill               | **FFmpeg / ffprobe**                                                | Inspeção, áudio e miniaturas em perfis restritos. [D14](GoblinShiftReferencias.md#d-3)                                                                                                       |
| Arquivos privados        | **Filesystem local na VPS**                                         | Uma interface de storage estreita; um backend compatível com S3 só entra quando houver motivo concreto (mais de um servidor, falta de disco). [D10](GoblinShiftReferencias.md#d-2)           |
| Identidade web           | **Google OIDC + GitHub OAuth 2.0 + Authlib**                        | Login externo e sessão própria revogável; sem gerenciar senhas. [D15](GoblinShiftReferencias.md#d-4)                                                                                         |
| Frontend                 | **React + TypeScript + Vite**                                       | Aplicação estática para configurar e acompanhar a API. [D21](GoblinShiftReferencias.md#d-5), [D22](GoblinShiftReferencias.md#d-5)                                                            |
| Rotas e dados remotos    | **React Router + TanStack Query**                                   | Navegação, mutações, cache em memória e polling. [D23](GoblinShiftReferencias.md#d-5)                                                                                                        |
| Interface                | **Tailwind CSS + shadcn/ui**                                        | Componentes selecionados, estilos e interação por teclado. [D24](GoblinShiftReferencias.md#d-5)                                                                                              |
| Contrato do cliente      | **openapi-typescript + fetch**                                      | Tipos derivados do OpenAPI e tratamento HTTP compartilhado. [D25](GoblinShiftReferencias.md#d-5)                                                                                             |
| Ambiente e proxy         | **Docker Compose + Nginx**                                          | Serviços da aplicação, TLS e mesma origem para frontend/API. [D30](GoblinShiftReferencias.md#d-6)                                                                                            |
| Isolamento do executor   | **gVisor (`runsc`) + `executor-ctl`**                               | Um container novo por tentativa, com kernel em espaço de usuário, criado por um serviço do host de interface mínima; o worker nunca acessa o Docker. [D17](GoblinShiftReferencias.md#d-4)    |
| Hospedagem               | **VPS KVM da Hostinger**                                            | Root completo, Docker, gVisor e filesystems dedicados ([3.8](#s3-8)). [D30](GoblinShiftReferencias.md#d-6)                                                                                   |
| Verificação              | **pytest, Ruff, mypy; Vitest e Playwright**                         | Regressões Python, tipos, componentes e jornadas de navegador. [D26](GoblinShiftReferencias.md#d-6), [D27](GoblinShiftReferencias.md#d-6), [D28](GoblinShiftReferencias.md#d-6)              |
| Dependências             | **uv + npm**                                                        | Ambiente Python e lockfiles Python/TypeScript. [D28](GoblinShiftReferencias.md#d-6)                                                                                                          |
| Diagnóstico              | **logging JSON + OpenTelemetry Python**                             | Erros correlacionados e rastreamento entre API, fila e execução. [D29](GoblinShiftReferencias.md#d-6)                                                                                        |

Versões exatas ficam nos lockfiles/imagens. **Python 3.14 é escolha deliberada:** em outubro de 2026, a 3.13 está em fase só de correções de segurança, a 3.14 recebe correções de bugs e a 3.15 acabou de sair. A troca é feita agora porque as regras Unicode do CSV Janitor seguem a versão fixada. Migrar para a 3.15 quando o ecossistema (Pillow, psycopg, pydantic-core) estiver maduro. Respeitar Starlette compatível com FastAPI. A stack ainda não foi testada como conjunto.

**FastAPI não executa codecs:** `async def` serve ao I/O; processamento durável fica em worker e sandbox, não em `BackgroundTasks`. [D02](GoblinShiftReferencias.md#d-1).

<a id="s2-2"></a>
### 2.2. Frontend e experiência de operação

SPA React e API na mesma origem, com estáticos do Vite. Sem runtime Node adicional, Next.js ou SSR; foco em uploads e acompanhamento.

| Tela                     | Conteúdo funcional                                                             |
|--------------------------|--------------------------------------------------------------------------------|
| **Oficina**              | Receitas disponíveis, presets e execuções recentes.                            |
| **Bancada**              | Upload, ordem das entradas, parâmetros, limites e execução.                    |
| **Execução / resultado** | Estado, tentativa, rejeições, comparação pertinente e downloads na mesma rota. |
| **Histórico**            | Filtros, disponibilidade dos resultados e reutilização de configuração.        |
| **Acesso**               | Sessão atual, emissão e revogação de chaves.                                   |

Polling a cada 2 s na tela ativa, com backoff em falhas e atualização ao retomar foco. Parar no estado terminal; banco prevalece sobre cache/animação. Sem WebSocket/SSE obrigatório.

Toda mutação feita pela SPA, inclusive o `PUT` de conteúdo via `XMLHttpRequest`, envia o header `X-CSRF-Token` lido do cookie `__Host-gs_csrf` ([3.6](#s3-6)). Diante de `REAUTH_REQUIRED`, a SPA refaz o login no mesmo provedor e volta à tela de origem sem perder o formulário.

Formulários usam estado local; TanStack Query guarda consultas. Sem Redux. Tipos OpenAPI não dispensam validação servidor. Componentes por receita usam os mesmos schemas, sem editor universal.

**Regras do cliente:**
- Consultas com polling usam `retry` 0 ou 1, para não multiplicar requisições com o polling, e nunca consultam antes do `Retry-After` recebido.
- O `ETag` de um preset é derivado do campo `revision` do corpo, de forma documentada, porque o cache do TanStack guarda só os dados e perderia o header.
- A `Idempotency-Key` é gerada com `crypto.randomUUID()` por intenção de envio, antes de chamar a mutação, e reaproveitada em retry ou duplo clique; mudar os parâmetros gera chave nova.
- Sem atualização otimista em execução, cancelamento ou presets: o servidor é quem decide o resultado.
- Cada tela tem uma única ação primária (Executar na Bancada, Baixar no resultado); Excluir é secundária e pede confirmação.
- Oficina, Histórico e resultados `expired` ou `missing` têm estados vazios desenhados, não telas em branco.
- **Upload:** o `PUT` de conteúdo usa `XMLHttpRequest`, porque o `fetch` não expõe progresso de envio (o envio por stream com `duplex: 'half'` não funciona em HTTP/1.1 nem no Safari). Progresso real em bytes, cancelamento com `abort()`, no máximo 2 envios simultâneos (1.7), estado "confirmando" entre 100% dos bytes e a resposta `200`, aviso `beforeunload` se a pessoa sair no meio, e falha que cria um upload novo, já que não há retomada. Esse percentual é legítimo porque vem de bytes; a execução continua sem percentual.
- **Download:** sempre por navegação (`<a href>` para a rota de download, com `attachment` do servidor), nunca `fetch` → `blob`, que carregaria até 512 MiB na memória do navegador.
- **Formulários por receita:** escritos à mão, um por receita, tipados pela união discriminada gerada do OpenAPI; defaults e limites vêm do catálogo em tempo de execução. O CI regenera os tipos e falha se houver diferença (`git diff`).

**Apresentação:** oficina 2D em HTML/CSS, com cartões e goblins discretos; sem canvas/engine, exceto a cena 3D da abertura da Oficina, carregada depois do conteúdo e nas condições do [ADR 027](adr/027-3d-na-abertura.md) e do [ADR 030](adr/030-motor-e-asset-do-3d-da-abertura.md). Ações: Executar, Cancelar, Baixar, Salvar preset, Excluir. Sem percentuais ou posição de fila inventados.

UI com teclado, foco, labels, erros por campo e movimento reduzido. Reordenar entradas por arrasto sempre tem alternativa com botões subir/descer (WCAG 2.2, 2.5.7). Mudanças de estado e de fase são anunciadas em região `aria-live="polite"`, só quando mudam, não a cada polling (4.1.3). Animações dos goblins com mais de 5 s têm controle de pausa (2.2.2). Estado nunca é comunicado só por cor. Preview CSV escapado: 100 registros/64 KiB; imagens apenas derivadas, PDF por mapa de páginas, mídia por miniaturas. Não renderizar uploads brutos inline. [D18](GoblinShiftReferencias.md#d-4), [D19](GoblinShiftReferencias.md#d-4), [D24](GoblinShiftReferencias.md#d-5).

<a id="s2-3"></a>
### 2.3. Notas de implementação da stack

A seção 1 diz o que o produto faz; esta seção registra como as bibliotecas escolhidas precisam ser usadas para cumprir isso, porque o comportamento padrão de cada uma diverge da spec em pontos específicos.

**Pillow** ([1.2](#s1-2), [D12](GoblinShiftReferencias.md#d-3)):
- Não usar `ImageOps.contain` nem `thumbnail`: o primeiro amplia imagens pequenas, e ambos arredondam as dimensões em vez de truncar. O código calcula as dimensões e chama `resize` diretamente, com `LANCZOS`.
- Limite de pixels: `MAX_IMAGE_PIXELS` só emite aviso acima de 1× o valor e só levanta `DecompressionBombError` acima de 2×, então não garante o teto. `Image.open` é preguiçoso: logo após abrir, antes de `load()`, `exif_transpose` ou conversão, validar `format`, `mode`, `n_frames`, largura × altura e cada eixo contra 1.7. Como segunda barreira, configurar `MAX_IMAGE_PIXELS` com o mesmo teto e transformar `DecompressionBombWarning` em erro.
- MPO: o Pillow abre como `MpoImageFile` com `n_frames > 1`; processar só o quadro 0 (`seek(0)`) e não confundir com animação, que continua rejeitada.
- Display P3: reconhecer o perfil pelo conteúdo (primárias e curva de transferência), não pela descrição textual, que varia entre aparelhos; converter com `ImageCms` (LittleCMS) para sRGB, intenção colorimétrica relativa. As fixtures incluem fotos reais de iPhone.
- Metadados: o padrão do `save` varia por formato (sem `icc_profile`, o JPEG sai sem perfil; outros metadados podem ser copiados), então ele sempre recebe `exif`, `icc_profile` e XMP de forma explícita.

**Módulo `csv`** ([1.3](#s1-3), [D11](GoblinShiftReferencias.md#d-3)):
- Abrir com `encoding="utf-8-sig"` e `newline=""`; reader com `strict=True`.
- A saída não usa o `csv.writer`: o `QUOTE_MINIMAL` dele não põe aspas num campo só por começar com tab, e o `tab_prefix` precisa do tab dentro de aspas. Um serializador próprio de poucas linhas segue a RFC 4180 (`\r\n`, aspas só quando necessárias), e um teste de propriedade confere a ida e volta com o `csv.reader`.
- Mesmo em modo estrito, o módulo aceita `ab"c` como literal; é por isso que a spec declara esse comportamento em vez de prometer rejeitá-lo.
- O reader não confere o número de campos de cada registro; o código faz isso.
- `field_size_limit` conta caracteres, não bytes, e é estado global do módulo: é ajustado uma vez no início do executor, como proteção grosseira. Os limites em bytes são medidos por um gerador que envolve o iterador de linhas e conta os bytes UTF-8 de cada registro.
- `line_num` conta linhas físicas, não registros; os relatórios usam um contador próprio.

**FFmpeg** ([1.5](#s1-5), [D14](GoblinShiftReferencias.md#d-3)): não receber URL não basta. Toda chamada usa `-nostdin`, demuxer forçado pela allowlist (`-f mov` ou `-f matroska`, conforme a assinatura detectada), `-protocol_whitelist file` e a entrada por caminho absoluto fixo.

**FastAPI** ([1.6](#s1-6), [D02](GoblinShiftReferencias.md#d-1)):
- Handlers próprios para `RequestValidationError`, `HTTPException` do Starlette e os 404/405 do roteador, porque por padrão o FastAPI responde `{"detail": …}`.
- Decodificação protegida do JSON de comandos: [3.6](#s3-6).
- Sessão do banco fora das transferências (`scope="function"` nas dependências com `yield`): [3.2](#s3-2).

[↑ Sumário](#sumario)

---

<a id="arquitetura"></a>
## 3. Arquitetura

<a id="s3-1"></a>
### 3.1. Monólito modular, processos separados

```text
React / cliente HTTP
        │ HTTPS
        ▼
      Nginx ─────────────────── arquivos estáticos React
        │
        ▼
FastAPI + Uvicorn ───────────── PostgreSQL ◄──────── manutenção
        │                   execuções = fila     (reconciliação e
        │                         │               retenção)
        │                         │ claim com SKIP LOCKED + NOTIFY
        │                         ▼
        │                  worker confiável
        │                         │ socket Unix restrito
        │                         ▼
        │            executor-ctl (serviço do host)
        │                         │ docker run --runtime=runsc
        │                         ▼
        │           executor descartável (gVisor)
        │           sem rede ou credenciais
        │                         │
        └──── storage privado ◄── coleta e publicação pelo worker
```

Código e banco únicos; API, worker, manutenção e executor são processos, não microserviços. Sem broker de mensagens, Redis, Kafka, Kubernetes, mesh ou event sourcing.

PostgreSQL mantém estados e a fila; storage, bytes; executor, transformação; `executor-ctl` é o único componente com acesso ao Docker.

**Por que fila no PostgreSQL, e não RabbitMQ + Celery:** com broker, aceitar trabalho exige gravar em dois armazenamentos, o que obriga a ter outbox, dispatcher, confirmação de publicação e consumidor idempotente, e ainda traz as armadilhas de `acks_late`, `reject_on_worker_lost` e do `consumer_timeout` de 30 minutos do RabbitMQ diante de tentativas de 2 × 10 minutos. Com a fila no banco, aceitar e enfileirar são a mesma transação, e o claim da execução e a criação da tentativa também: não existe dupla escrita para reconciliar. Na escala do serviço (uma tentativa física por vez, no máximo 10 na fila), a contenção é desprezível; a DDIA (cap. 12) alerta para bancos usados como fila sob alto volume, que não é o caso. A troca de broker continua possível por trás de uma interface `JobQueue` estreita, se um dia houver vários workers em máquinas diferentes. [D08](GoblinShiftReferencias.md#d-2).

<a id="s3-2"></a>
### 3.2. Módulos e dependências

Classificação dos subdomínios ([B13](GoblinShiftReferencias.md#l-4), cap. 1 e 10): o **core** recebe modelo de domínio e testes mais rigorosos; o **supporting** usa transaction scripts com a Session direto; o **generic** é comprado pronto. Um estilo único para tudo geraria complexidade acidental.

| Módulo       | Subdomínio | Segredo que esconde                                                                                            | Interface pública                                                                                                  | Pode depender de                          |
|--------------|------------|----------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|-------------------------------------------|
| `processing` | Core       | Regras de cada receita, nomes de saída, manifesto e empacotamento                                              | `run` e `package` (contrato abaixo) e o registro de receitas                                                       | Nada do projeto                           |
| `runs`       | Core       | Ciclo de vida da Run, claim, fencing, publicação, retentativa, reconciliação, cotas, cliente do `executor-ctl` | `accept`, `cancel`, `get`, `list`, `results`, laço do worker e da manutenção                                       | `catalog`, `files`, `presets`, `platform` |
| `files`      | Supporting | Storage físico, `StoredObject`, usos ativos, retenção, exclusão adiada, downloads                              | Intenções de upload, recebimento de conteúdo, `hold`/`release` de usos, abertura para download, pedido de exclusão | `platform`                                |
| `catalog`    | Supporting | Quais receitas e versões existem, schemas, defaults e estado de cada versão                                    | Listar, obter versão, resolver defaults                                                                            | `processing`, `platform`                  |
| `presets`    | Supporting | Revisões e validação de presets                                                                                | CRUD com revisão                                                                                                   | `catalog`, `platform`                     |
| `identity`   | Generic    | OIDC/OAuth (Authlib), sessões, CSRF, chaves, escopos, estado da conta                                          | `authenticate(request) -> Principal`, `require_scope`, `require_recent_auth`                                       | `platform`                                |
| `capacity`   | Supporting | Livro-razão de reservas de armazenamento (`CapacityPool`, `ResourceReservation`), por conta e no ambiente ([ADR 024](adr/024-livro-razao-de-capacidade.md)) | `reserve`, `release`, `transfer`, `usage`, `reconcile`                                                             | `platform`                                |
| `platform`   | Técnico    | Engine e unidade de trabalho do banco, settings, logging, telemetria                                           | `unit_of_work()`, settings, logger                                                                                 | Nada do projeto                           |
| Entrypoints  | —          | Tradução de HTTP e CLI e os laços dos processos `api`, `worker`, `maintenance`, `cli`                          | —                                                                                                                  | Todos os módulos acima                    |

O grafo é acíclico. Os módulos recebem `owner_id` do entrypoint, já autenticado, em vez de dependerem de `identity`. `files` não conhece execuções: `runs` registra o uso dos objetos com `hold` na aceitação e o libera com `release` no estado terminal, e a exclusão consulta só esses usos. As regras são verificadas no CI com import-linter ([D28](GoblinShiftReferencias.md#d-6)), como fitness function ([B14](GoblinShiftReferencias.md#l-4), cap. 6 e 11):
- contrato `layers`: entrypoints → `runs` → `presets` → (`catalog`, `files`, `identity`) → `capacity` → (`processing`, `platform`);
- contrato `forbidden`: `processing` não importa `sqlalchemy`, `fastapi`, `starlette`, `authlib`, `httpx` nem outro módulo do projeto (com `include_external_packages = true`);
- contrato `independence`: `catalog`, `files` e `identity` não importam uns aos outros;
- um teste confirma que a imagem do executor não consegue importar `sqlalchemy`.

**Agregados.** `Run` é a raiz do agregado de execução: `Attempt`, `RunInput` e `RunOutput` são entidades internas e só mudam por operações de `runs` sobre a Run travada (`RunRepository.get_for_update`). `Upload` e `Artifact` pertencem a `files` e referenciam um `StoredObject` por ID. Exceção pragmática, registrada em ADR ([3.10](#s3-10)): a publicação grava na mesma transação curta o estado terminal da Run, os `RunOutput` e os `Artifact`, porque publicar parte das saídas seria pior que acoplar dois agregados numa transação ([B13](GoblinShiftReferencias.md#l-4), cap. 5).

Rotas traduzem HTTP; serviços delimitam transações; transformações não dependem de FastAPI. Não persistir modelos Pydantic integralmente por conveniência.

**Contrato de receita.** `(recipe_id, recipe_version)` resolve a implementação num registro simples (`dict` de chave para implementação), sem hierarquia de classes: Template Method não serve, porque Detox e CSV Janitor operam sobre o lote inteiro. A Run fixa digest, parâmetros e limites.

```python
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class InputFile:
    position: int  # ordem da entrada, a partir de 0
    path: Path  # somente leitura, dentro do sandbox
    original_name: str  # dado, nunca caminho
    size: int
    sha256: str


@dataclass(frozen=True)
class ReportWarning:
    code: str
    detail: str


@dataclass(frozen=True)
class ItemOk:
    position: int
    outputs: Sequence[str]  # nomes finais criados em OutputDir
    warnings: Sequence[ReportWarning] = ()
    counts: Mapping[str, int] = field(default_factory=dict)  # contagens próprias da receita


@dataclass(frozen=True)
class ItemRejected:
    position: int
    code: str  # código do catálogo de 1.6
    reason: str


@dataclass(frozen=True)
class Report:
    items: Sequence[ItemOk | ItemRejected]  # um por InputFile, na ordem
    warnings: Sequence[ReportWarning]


class OutputRole(StrEnum):
    CONTENT = "content"
    PREVIEW = "preview"


@dataclass(frozen=True)
class OutputFile:
    name: str  # nome final, com as regras de 3.6 já aplicadas
    stream: BinaryIO


class OutputDir(Protocol):
    def create(self, logical_name: str, role: OutputRole, position: int) -> OutputFile:
        """Aplica as regras de nome de 3.6 e os tetos de bytes e quantidade."""
        ...


class Progress(Protocol):
    def item_done(self, position: int) -> None: ...


class Recipe[P](Protocol):
    """P é o modelo de parâmetros validados da versão da receita."""

    def run(
        self, params: P, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report: ...
```

- Rejeição de um arquivo é um `ItemRejected(position, code, reason)` no relatório; `RecipeFailure(code)` é falha determinística da execução; qualquer outra exceção é infraestrutura (catálogo em [1.6](#s1-6)).
- `create` recebe a `position` da entrada porque a colisão de nomes se resolve com sufixo pelo índice original ([3.6](#s3-6)), e devolve o nome final porque o `ItemOk` cita o que foi criado. `ItemOk` carrega `counts` e `warnings` porque o relatório os informa por arquivo.
- `NO_VALID_OUTPUTS` não é `RecipeFailure`: o worker o decide na publicação, quando o relatório não tem nenhum `ItemOk`. Um job com mais entradas que o `max_inputs` da versão viola o contrato do executor (código de saída 4, infraestrutura); não há código novo no catálogo.
- Manifesto e ZIP são feitos por uma função comum, `package(report, out_root, spec, limits)` (`goblinshift.processing.packaging`), executada no próprio executor depois de gravar o `report.json`; nenhuma receita monta ZIP (contrato do import-linter). O `PackageSpec` leva receita, versão, digest da imagem (do `context.json`, [ADR 021](adr/021-digest-da-imagem-em-context-json.md)), parâmetros resolvidos e entradas, porque o manifesto precisa deles e o `Report` não os tem. Manifesto acima de 8 MiB falha com `REPORT_LIMIT_EXCEEDED`; ZIP acima de 512 MiB, com `OUTPUT_LIMIT_EXCEEDED` ([3.6](#s3-6)).
- O executor não tem rede: o progresso é escrito em `progress.jsonl` na área de trabalho, e o worker o lê e atualiza `phase` e `progress` com fencing.

**Demais interfaces:**
- *Storage* (`files`): `create_exclusive(key, chunks) -> (sha256, size)`, `open(key)`, `stat(key)`, `delete(key)`.
- *Runner* (`runs`): `start(attempt_id)` idempotente, `status(attempt_id)`, `stop(attempt_id, grace)`, `remove(attempt_id)`. A implementação de produção fala com o `executor-ctl` e manda no `start` a imagem fixada na Run; a local, em subprocesso, só existe fora de produção (na VPS, valeu até a fatia 4, [ADR 016](adr/016-runner-local-na-vps-ate-a-fatia-4.md), hoje substituído).
- *Identidade* (`identity`): `authenticate(request) -> Principal(owner_id, scopes, via)`, em que `via` é sessão ou chave; `require_scope` e `require_recent_auth`.

**Abstrações:** as interfaces acima; Strategy pode ser função. Composição/funções puras para filtros, nomes e contagens. Evitar repository genérico, service locator, barramento e classe por endpoint.
- **Repositório só onde há agregado:** um `RunRepository` específico (`get_for_update`, `add`) para a Run e suas entidades internas. Presets, catálogo e identidade usam a Session direto, em transaction scripts.
- **Unidade de trabalho:** um context manager fino que abre a transação, faz commit explícito e rollback por padrão. Ele concentra num lugar só a regra "commit incerto → reler antes de repetir" de 3.4.
- **Timeouts em toda integração:** `statement_timeout` de 5 s na API e de 30 s nos lotes de manutenção, `lock_timeout` de 2 s, `idle_in_transaction_session_timeout` de 30 s como rede de segurança contra transações esquecidas, 10 s nas chamadas ao `executor-ctl` (30 s para `start`) e 5 s nas chamadas ao Google e ao GitHub.
- **Threads e conexões dimensionadas juntas:** o threadpool do FastAPI (o limite padrão do AnyIO é 40 threads) e o pool do SQLAlchemy precisam ser coerentes; `pool_timeout` curto devolve `503` em vez de deixar a requisição pendurada esperando conexão.

**Banco síncrono:** uma SQLAlchemy Session por unidade de trabalho, sem compartilhamento entre threads, requests ou ciclos do worker. Handler bloqueante curto usa `def`; `async def` desloca I/O bloqueante para executor limitado. Nunca manter transação durante codec ou transferência, e isso exige um cuidado específico do FastAPI:
- Uma dependência com `yield` tem, por padrão, `scope="request"`: o código depois do `yield` só roda depois de a resposta inteira ser enviada, inclusive após um download por streaming de 10 minutos. Como a Session do SQLAlchemy 2 abre transação na primeira consulta (autobegin), a conexão ficaria *idle in transaction* durante toda a transferência.
- Regra: dependências que entregam Session usam `Depends(..., scope="function")` (FastAPI ≥ 0.121.0), que fecha a Session antes de a resposta ser enviada; serviços usam blocos `with session.begin()` explícitos.
- Rotas de envio e download de conteúdo autorizam e leem metadados numa transação curta, encerrada antes do primeiro byte transferido, e registram o resultado numa segunda transação curta, depois da transferência.

[D02](GoblinShiftReferencias.md#d-1), [D06](GoblinShiftReferencias.md#d-2).

<a id="s3-3"></a>
### 3.3. Modelo persistido

| Registro              | Conteúdo essencial                                                                                                                                                                                                                                                                                                                     |
|-----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `User`                | ID interno, `issuer`, `subject`, nome de exibição, estado (`active`/`blocked`), versão e data dos termos aceitos. Unicidade de `(issuer, subject)`.                                                                                                                                                                                    |
| `WebSession`          | Hash do segredo, usuário, criação, `authenticated_at` (login no provedor), `last_seen_at`, expiração absoluta e revogação. O nome evita confusão com a Session do SQLAlchemy.                                                                                                                                                          |
| `ApiKey`              | Identificador público, hash do segredo, usuário, escopos, expiração e revogação.                                                                                                                                                                                                                                                       |
| `Preset`              | Proprietário, nome, receita/versão, parâmetros e revisão. Execuções guardam `preset_id` opcional com `ON DELETE SET NULL`, para que excluir um preset não altere o histórico.                                                                                                                                                          |
| `Upload`              | Proprietário, nome original, `object_id` (FK para `StoredObject`), estado e retenção. Incompleto expirado perde linha e objeto; pronto expirado ou excluído mantém a linha com `deleted_at`, porque `RunInput` a referencia.                                                                                                           |
| `Run`                 | Proprietário, receita/versão/digest, parâmetros e limites fixados, estado, fase, `generation`, `claim_count`, `eligible_at`, `queue_expires_at`, `deadline_at`, intenção de cancelamento, razão de parada, `traceparent` da requisição de aceitação, datas e resumo. A própria tabela é a fila.                                        |
| `RunInput`            | Chave primária `(run_id, position)` e `UNIQUE (run_id, upload_id)`, o que impede no banco a repetição de entradas. O resultado por arquivo (`outcome`, `error_code`) fica em colunas, porque é consultado e filtrado.                                                                                                                  |
| `Attempt`             | Execução, número (1 a 4, um por claim, com `CHECK`), `generation`, nome do container (`gs-exec-{attempt_id}`), `lease_expires_at`, `executor_started_at`, tempos e erro. Só a Attempt com `executor_started_at` conta como tentativa física.                                                                                           |
| `Artifact`            | Proprietário, `object_id` (FK para `StoredObject`; o Detox aponta para o mesmo objeto da entrada), papel (`output`, `report`, `manifest`, `package`, este o ZIP, ou `preview`, com `source_artifact_id` da saída de origem; [ADR 019](adr/019-layout-de-out-pacote-e-papeis.md), [ADR 037](adr/037-previews-em-grupo-e-papel-proprios.md)), `file_name` (nome final da saída, usado no download), disponibilidade e retenção.                                                                                                                     |
| `StoredObject`        | Proprietário, `storage_key` único, SHA-256, bytes (`bigint`), estado (`pending`, `committed`, `delete_requested`, `deleted`, `missing`) e, para candidatos, tentativa e geração. Supertabela comum a `Upload` e `Artifact`: substitui o antigo "objeto ou referência física", que exigiria uma associação polimórfica sem FK possível. |
| `ObjectUse`           | Objeto, tipo do detentor (`run` ou `download`), ID do detentor, início e fim. A remoção física só acontece quando não há uso aberto.                                                                                                                                                                                                   |
| `RunOutput`           | Vínculo ordenado entre execução, artefato e nome lógico de saída.                                                                                                                                                                                                                                                                      |
| `IdempotencyRecord`   | Chave primária `(owner_id, operation, idem_key)`, hash do comando (`bytea` de 32 bytes), execução e expiração. Reuso após o prazo: `INSERT … ON CONFLICT … DO UPDATE … WHERE expires_at <= now()`.                                                                                                                                     |
| `ResourceReservation` | Livro-razão: uma linha por reserva (conta, execução ou upload, recurso, quantidade em `bigint`, `released_at`). Liberação idempotente: `UPDATE … SET released_at = now() WHERE released_at IS NULL RETURNING amount`.                                                                                                                  |
| `CapacityPool`        | Escopo (global ou conta), recurso, `max_amount` e `reserved` em `bigint`, com `CHECK (reserved BETWEEN 0 AND max_amount)`. Reserva: `UPDATE … SET reserved = reserved + :n WHERE reserved + :n <= max_amount`; nenhuma linha afetada → `429` ou `503`. A manutenção recalcula o pool a partir do livro-razão.                          |
| `AuditEvent`          | Conta, tipo do evento, momento, IP truncado e resultado; sem nomes de arquivo ou conteúdo. Retenção de 90 dias.                                                                                                                                                                                                                        |

Chaves estrangeiras e unicidade protegem vínculos, revisão de presets, ordem de entradas, uma tentativa viva por execução (índice único parcial), geração e publicação lógica. Relações privadas carregam o proprietário, sem exceção: `UNIQUE (owner_id, id)` em `Upload`, `Run`, `Artifact` e `StoredObject`, e os vínculos usam FKs compostas `(owner_id, x_id)` com colunas `NOT NULL` (uma FK com coluna nula não é verificada), de modo que o próprio banco impede ligar recursos de contas diferentes. FKs não criam índice do lado que referencia; cada uma ganha o seu. Bytes são sempre `bigint`: 4 GiB por conta já passam do limite de `integer`. Exclusão usa `RESTRICT` por padrão; `CASCADE` só da Run para seus vínculos internos, no expurgo. Detalhes limitados por item ficam em JSONB validado ou no relatório; não criar tabela por linha CSV, nó ou etapa visual.

As cotas diárias são calculadas a partir de `Run` e `RunInput` das últimas 24 horas, sem tabela de contadores própria.

Estados e fases são `text` com `CHECK` nomeado, não ENUM nativo: o Alembic autogenerate não detecta mudança de valores de ENUM nem de expressões de `CHECK`, então toda migração é revisada à mão. O histórico é paginado com comparação de linha, `(created_at, id) < (:c, :i)`, sobre o índice `(owner_id, created_at DESC, id DESC)`; nunca com `OFFSET`.

Todas as consultas privadas partem da identidade autenticada. `owner_id` não é aceito como autoridade no corpo da requisição. IDs aleatórios dificultam enumeração, mas não substituem autorização.

<a id="s3-4"></a>
### 3.4. Trabalho durável e publicação única

**Aceitação.** Validar proprietário, estado da conta, versão, parâmetros, uploads prontos, cotas diárias e limites. Em uma transação curta: resolver a idempotência, verificar a fila global (cheia → `503` com `Retry-After`), reservar capacidade, vincular entradas e gravar a `Run` com `status=queued`, `eligible_at=now()` e `queue_expires_at`. Gravar a Run já é enfileirar; o mesmo commit emite `NOTIFY goblinshift_runs`. Responder `202` apenas após commit. O pedido não depende de a conexão HTTP permanecer aberta.

**Claim.** O worker espera com `LISTEN goblinshift_runs` e, como garantia contra notificações perdidas, consulta a cada 2 segundos. Numa transação curta, reivindica a próxima execução elegível:

```sql
SELECT … FROM run
WHERE eligible_at <= now()
  AND ((status = 'queued' AND queue_expires_at > now())
       OR (status = 'running' AND phase = 'retry_wait'))
ORDER BY created_at, run_id
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

Na mesma transação: incrementar `Run.generation`, criar a `Attempt` com essa geração, o número do claim (1 a 4) e `lease_expires_at`, passar a Run para `running` e, na primeira tentativa, gravar `deadline_at = now() + 30 min` com o relógio do banco. Um índice único parcial (uma `Attempt` sem `ended_at` por Run) impede duas tentativas simultâneas mesmo diante de um bug. Só depois do commit o worker pede o container ao `executor-ctl` ([3.6](#s3-6)), com o nome determinístico `gs-exec-{attempt_id}`; repetir o pedido adota o container existente em vez de criar outro. [D08](GoblinShiftReferencias.md#d-2), [D09](GoblinShiftReferencias.md#d-2).

**Fencing.** A geração é o token que separa a tentativa válida de um worker atrasado. Ela só muda no claim; adotar um container já existente não a altera.
- Toda escrita do coordenador sobre a execução é condicional, não só a publicação: renovação do lease, fase, progresso, fim da tentativa, erro e liberação de reservas usam `UPDATE … WHERE run_id = :run AND generation = :gen AND status IN (…)`.
- Se nenhuma linha for afetada, outra tentativa ou o reconciliador assumiu. O coordenador para imediatamente, sem escrever mais nada, e a limpeza fica com o reconciliador.
- Candidatos ficam em chaves `runs/{run_id}/g{generation}/…`, o que diz ao coletor de lixo exatamente o que pertence a uma geração vencida.
- Cenário coberto: um worker pausado (VM suspensa, SIGSTOP) volta depois de o reconciliador ter decidido e tenta gravar `phase=packaging` ou liberar a reserva de outra tentativa; a escrita simplesmente não acontece.

**Publicação.** O executor termina; o coordenador confere resultados, grava objetos em chaves exclusivas e, numa transação curta, publica `Artifact`/`RunOutput` e o estado terminal condicionando o `UPDATE` da Run a `generation = :gen`, `status = 'running'`, ausência de intenção de cancelamento e `now() <= deadline_at`. O prazo é conferido com o relógio do PostgreSQL, dentro da mesma transação, nunca com o relógio do worker. Se o `UPDATE` não afetar a linha, a publicação não aconteceu, e o motivo (cancelamento, prazo ou outra geração) é relido do banco.

Coleta e gravação dos candidatos podem demorar (o ZIP chega a 512 MiB): o coordenador segue renovando o lease entre blocos, como durante a execução, e erro de E/S nelas (disco do storage cheio, área ilegível) é falha transitória na hora, não espera o lease vencer. Saídas anteriores ao commit são candidatos privados. Se o resultado do commit for incerto, reler por identificadores estáveis antes de apagar candidatos ou repetir a publicação. Unicidade e geração impedem publicação lógica duplicada; não prometem que o cálculo físico ocorrerá exatamente uma vez.

**Retentativa.** Até duas tentativas físicas no total para falha transitória, dentro do `deadline_at`. Só conta como física a `Attempt` que registrou `executor_started_at`, isto é, cujo executor começou a trabalhar. Lease vencido sem executor confirmado reagenda a Run em `retry_wait` pelo mesmo caminho, sem gastar tentativa física. Não há retry em memória nem dentro de biblioteca: o coordenador encerra a tentativa e, numa escrita com fencing, deixa a Run em `running` com `phase=retry_wait` e `eligible_at = now() + 5 a 30 s`, com jitter. A nova tentativa passa pelo claim normal, recebe nova geração e recomeça o lote; não há checkpoint por arquivo. Arquivo inválido, erro determinístico e limite de conteúdo não recebem retry. Não multiplicar retries entre serviço e executor.

**Limite de claims.** Cada claim cria uma `Attempt` (`number` de 1 a 4, `CHECK` no banco) e incrementa `Run.claim_count`. Como claim sem executor iniciado não gasta tentativa física, o limite é de claims, não de tentativas: o 5º claim é uma mensagem venenosa (leases vencendo seguidamente) e termina `failed` com `INFRA_POISON`, em vez de ocupar o único executor para sempre.

**Reconciliação.** O processo de manutenção roda em lotes, reivindicando linhas com `FOR UPDATE SKIP LOCKED`: tentativa com lease vencido, execução na fila com `queue_expires_at` vencido (termina `failed` com `QUEUE_EXPIRED`), execução com `deadline_at` vencido, candidatos de gerações vencidas e exclusões incompletas. Lease vencido não prova que o executor morreu: antes de liberar uma execução, a manutenção consulta o `executor-ctl` pelo nome do container e só reagenda depois de confirmar que ele terminou ou de pará-lo. As transições feitas pela manutenção usam a geração lida como condição, pela mesma regra de fencing, e a liberação de reservas é idempotente. Apagar é a direção perigosa, então a manutenção tem um limitador (*Release It!*, Governor): no máximo 500 objetos ou 2 GiB removidos por ciclo; acima disso, para e alerta o administrador.

Em operações que alteram vários recursos, manter ordem de locks: execução → tentativa → arquivos e, por último, a capacidade, sempre o pool da conta antes do pool global, com IDs ordenados dentro de cada grupo. O pool global é a linha mais disputada: travado por último, ele fica preso o menor tempo possível. Publicação e cancelamento disputam o mesmo registro da execução. Esperas de rede e codecs acontecem fora dessas transações. Cada fluxo trava exatamente isto:

| Fluxo              | Linhas travadas, na ordem                                                  | Observação                                                                                             |
|--------------------|----------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| Aceitação          | Fila global (advisory) → nova Run → Uploads (`FOR SHARE`) → capacidade da conta → capacidade global | A trava da fila serializa as aceitações e cobre a cota diária                                          |
| Claim              | Run (`FOR UPDATE SKIP LOCKED`) → nova Attempt                              | Não toca capacidade                                                                                    |
| Publicação         | Run → Attempt                                                              | Reservas são liberadas numa transação seguinte, para não travar capacidade depois da Run               |
| Cancelamento       | Run                                                                        | Disputa a mesma linha que a publicação                                                                 |
| Exclusão de upload | Upload (`FOR UPDATE`) → StoredObject                                       | Marca `delete_requested`; a remoção física fica com a manutenção, quando não houver `ObjectUse` aberto |
| Manutenção         | Run (`SKIP LOCKED`) → Attempt; reservas numa transação seguinte            | Mesma regra da publicação                                                                              |
| `DELETE /me`       | Conta → Runs → Uploads                                                     | Execução não terminal é cancelada antes                                                                |

Deadlock (`40P01`) e falha de serialização repetem a transação inteira, com limite de tentativas. [D05](GoblinShiftReferencias.md#d-2).

<a id="s3-5"></a>
### 3.5. Estados, cancelamento e recuperação

Estados: `queued`, `running`, `stopping`, `succeeded`, `succeeded_with_warnings`, `canceled` e `failed`. `phase` só existe em `running` (`inspecting`, `processing`, `packaging`, `finalizing`, `retry_wait`). A tabela é a especificação completa; a implementação é uma função pura, testada sem banco.

| De                       | Evento                                                                  | Guarda                                                     | Para                                                                    | Efeitos                                                                   |
|--------------------------|-------------------------------------------------------------------------|------------------------------------------------------------|-------------------------------------------------------------------------|---------------------------------------------------------------------------|
| `queued`                 | Claim                                                                   | `eligible_at` e `queue_expires_at` válidos; claims < 4     | `running` / `inspecting`                                                | `generation` + 1, nova Attempt, `deadline_at` na primeira tentativa       |
| `queued`                 | Cancelamento pedido                                                     | —                                                          | `canceled`                                                              | Sem tentativa; libera reservas e usos                                     |
| `queued`                 | Fila vencida                                                            | `now() > queue_expires_at`                                 | `failed` (`QUEUE_EXPIRED`)                                              | Libera reservas e usos                                                    |
| `running`                | Publicação                                                              | Geração atual, sem cancelamento, `now() <= deadline_at`    | `succeeded`, `succeeded_with_warnings` ou `failed` (`NO_VALID_OUTPUTS`) | Publica saídas; encerra a Attempt; libera reservas e usos em seguida      |
| `running`                | Falha determinística                                                    | Executor terminou com `RecipeFailure` ou limite de recurso | `failed` (código da falha)                                              | Encerra a Attempt; descarta candidatos                                    |
| `running`                | Falha transitória                                                       | Ainda há tentativa física e prazo                          | `running` / `retry_wait`                                                | Encerra a Attempt; `eligible_at` com jitter                               |
| `running`                | Lease vencido, executor parado                                          | Nenhum início do executor registrado (`executor_started_at` vazio) | `running` / `retry_wait`                                                | Encerra a Attempt sem gastar tentativa física; `eligible_at` com jitter   |
| `running` / `retry_wait` | Claim                                                                   | `eligible_at` atingido; claims < 4                         | `running` / `inspecting`                                                | `generation` + 1, nova Attempt                                            |
| `running` / `retry_wait` | Cancelamento ou prazo vencido                                           | Nenhum executor vivo                                       | `canceled` ou `failed` (`DEADLINE_EXCEEDED`)                            | Libera reservas e usos                                                    |
| `running`                | Cancelamento, prazo vencido ou falha transitória sem tentativa restante | Executor possivelmente vivo                                | `stopping`                                                              | Grava a razão de parada (a primeira vence); pede `stop` ao `executor-ctl` |
| `stopping`               | Término do executor confirmado                                          | `status` do `executor-ctl` confirma o fim                  | `canceled` ou `failed` (código da razão)                                | Descarta candidatos; libera reservas e usos                               |
| `running` / `retry_wait` | Claim recusado por excesso de claims                                    | claims = 4                                                 | `failed` (`INFRA_POISON`)                                               | Libera reservas e usos                                                    |

Invariantes, verificadas por testes de máquina de estados com Hypothesis ([3.7](#s3-7)): estado terminal nunca muda; `canceled` nunca tem `RunOutput`; no máximo duas tentativas físicas e quatro claims; nunca duas tentativas vivas ao mesmo tempo; a primeira razão de parada persistida é a que vale. Reprocessamento solicitado pelo cliente cria outra `Run`; histórico e parâmetros aceitos permanecem inalterados.

Cancelamento persiste a intenção e impede novos inícios e publicação final. O worker pede a parada ao `executor-ctl`, que concede até 10 segundos e força o encerramento quando necessário. Só confirmar `canceled` depois de verificar que o executor e seus descendentes terminaram. Uma execução ainda na fila pode ser cancelada sem criar tentativa.

Se sucesso foi confirmado primeiro, permanece sucesso. Se a intenção de cancelamento foi confirmada primeiro, o resultado não é publicado. A primeira razão de parada persistida determina `canceled` ou `failed`; ocorrências posteriores continuam no diagnóstico.

Lease expirado não prova que o processo morreu. Runtime inacessível mantém a execução em encerramento/reconciliação e conserva a reserva física. Não iniciar tentativa substituta até confirmar o término anterior ou conter fisicamente sua execução. Um deadline vencido também não autoriza anunciar que o processo parou. Prazo vencido é uma razão de parada como o cancelamento: disputa a mesma linha da Run e, se for persistido primeiro, impede a publicação.

Queda da API não cancela trabalho aceito. Queda do worker pode deixar um container ativo; o lease vence e a manutenção assume pelo nome determinístico. Falha do banco impede aceitar, iniciar e publicar trabalho, e também consultas e downloads, que dependem da autorização no banco. Queda do `executor-ctl` impede iniciar e parar containers; execuções aguardam dentro dos prazos e a manutenção não reagenda nada que não consiga inspecionar.

<a id="s3-6"></a>
### 3.6. Arquivos, identidade e isolamento

**Upload.** Criar intenções em lote, cada uma com nome e tamanho esperado, depois enviar os bytes de cada arquivo. Uma atualização condicional admite apenas um escritor: `created → receiving → ready`; falha leva a `failed`, sem reabrir o mesmo objeto para sobrescrita. Envio interrompido exige consultar o estado e, sem arquivo pronto confirmado, criar novo upload.

**Comandos JSON.** O FastAPI, quando a rota declara um modelo Pydantic no corpo, decodifica com o `json` da biblioteca padrão, que "ignores all but the last name-value pair", aceita `NaN` e `Infinity` e não impõe limite de tamanho nem de profundidade. Para cumprir 1.7, as rotas de comando não declaram o modelo como corpo:
1. Leem o corpo por `request.stream()`, contando bytes e abortando com `413` acima de 64 KiB, sem depender de `Content-Length`.
2. Decodificam com `object_pairs_hook` (chave duplicada → `400`) e `parse_constant` (`NaN`, `Infinity` → `400`) e checam a profundidade máxima de 16.
3. Só então validam com Pydantic, em modo estrito e com campos extras proibidos.

O schema continua publicado no OpenAPI por `openapi_extra`, para que clientes e tipos gerados não percam o contrato. [D02](GoblinShiftReferencias.md#d-1), [D03](GoblinShiftReferencias.md#d-1), [D11](GoblinShiftReferencias.md#d-3).

Contar bytes reais e calcular SHA-256 durante o streaming; recusar tamanho diferente do declarado. Confirmar `ready` somente após persistência. Extensão e Content-Type enviados pelo cliente são indícios, não validação do conteúdo. A inspeção profunda acontece no executor, não no handler com credenciais.

O storage é o filesystem da VPS, com criação exclusiva (`O_CREAT | O_EXCL`) fora da raiz pública. A interface cobre só criar, ler, consultar metadados e excluir. Se um backend compatível com S3 entrar no futuro, ele cumpre o mesmo contrato: bucket privado, chaves exclusivas, escrita condicional, e ETag não é SHA-256. [D10](GoblinShiftReferencias.md#d-2).

**Exclusão sempre adiada.** Excluir um upload ou uma saída torna o recurso indisponível na hora e marca o objeto como `delete_requested`. A manutenção remove o arquivo só quando nenhum `Upload` ou `Artifact` válido aponta para ele e não há `ObjectUse` aberto (execução ou download em andamento). Por isso excluir nunca responde "arquivo em uso" ([B03](GoblinShiftReferencias.md#l-1), cap. 10: adiar a exclusão "defines errors out of existence"). É também o que torna seguro o reaproveitamento do Detox: o mesmo objeto, duas referências.

**Nomes e pacotes.** Nomes recebidos são dados, nunca caminhos. Rejeitar NUL, controles, separadores e traversal. Formato invisível do Unicode (categoria Cf: U+202E e os demais controles bidi, U+200B, U+FEFF no meio do nome, os isolates U+2066 a U+2069) conta como controle: ao derivar o nome de saída do nome enviado, controles e Cf viram `_`; onde o nome já chega pronto (`OutputDir`, coleta do worker, `name_prefix` do Detox), são recusados. Decidido na fatia 5, antes de o serviço abrir, mudando também os nomes de saída da v1 do CSV Janitor. Nomes de saída utilizam NFC, extensão preservada, até 180 bytes por componente e 240 bytes por caminho. Comparar colisões com `casefold()` aplicado depois da normalização NFC; resolver com sufixo pelo índice original. Reservar `manifest.json` e `report.json`; não permitir overwrite. No ZIP, arquivos já comprimidos (JPEG, PNG, ZIP) vão com `ZIP_STORED`, sem gastar CPU, e texto com `ZIP_DEFLATED`; entradas em ordem estável e `date_time` fixo (1980-01-01) tornam o pacote determinístico para os mesmos dados.

O pacote contém conteúdo e os dois relatórios. O manifesto lista todas as entradas, hashes, bytes, parâmetros, versão/digest, nomes finais, duplicatas, rejeições, avisos e linhagem. Não contém segredos, caminhos reais, links temporários, seu próprio hash nem o hash do ZIP que o contém. Hashes finais dos relatórios e do pacote ficam nos metadados externos. Se o mapa obrigatório não couber no teto, falhar com `REPORT_LIMIT_EXCEEDED`.

**Saídas a partir da fatia 5.** `out/` tem no máximo um nível de subpasta, só com os grupos `images/`, `thumbnails/`, `tables/`, `text/`, `archives/` e `previews/` (este fora do manifesto e do ZIP, [ADR 037](adr/037-previews-em-grupo-e-papel-proprios.md)); na raiz ficam `report.json`, `manifest.json` e `<recipe_id>.zip` (por exemplo `image-forge.zip`), nomes reservados só na raiz. O worker publica cada saída de conteúdo como `Artifact` de papel `output`, com `file_name` igual ao caminho com o grupo (`images/foto.jpg`; o download anexa só o último componente), e o relatório, o manifesto e o ZIP com os papéis `report`, `manifest` e `package` ([ADR 019](adr/019-layout-de-out-pacote-e-papeis.md)). Saída idêntica a uma entrada (SHA-256 e tamanho calculados pelo worker) reaproveita o objeto da entrada enquanto ele estiver `committed` ([ADR 020](adr/020-detox-reaproveita-o-objeto-da-entrada.md)). Na fatia 3, antes do empacotador, só existiam os papéis `output` e `report`. O PDF Press grava os documentos e o `page-map.json` na raiz; o worker passa ao executor, no `context.json`, o `upload_id` de cada entrada (`input_upload_ids`), para o `select` citar uploads ([ADR 035](adr/035-pdf-press-com-pikepdf.md)).

**Identidade web.** Dois provedores via Authlib, ambos com Authorization Code, `state`, PKCE S256 e redirect exato por provedor:
- **Google (OpenID Connect):** escopo `openid` e `nonce`. Validar assinatura, emissor (`https://accounts.google.com` ou `accounts.google.com`), audiência e expiração do ID token pela biblioteca. Identidade: `(issuer, sub)`.
- **GitHub (OAuth 2.0, não OIDC):** não há ID token nem `nonce`. O código é trocado no servidor, com o client secret e o `code_verifier` (o GitHub aceita PKCE S256 desde julho de 2025), por um access token sem escopos. Esse token serve para uma única chamada a `GET https://api.github.com/user` e é descartado, nunca guardado. Identidade: `("https://github.com", id numérico)`.

Identificar usuário por `(issuer, subject)`, não por e-mail; não vincular contas automaticamente por endereço. Conta `blocked` não completa o login. O provedor de login não recebe acesso a arquivos do GoblinShift. [D15](GoblinShiftReferencias.md#d-4).

**Sessão.** Depois do login, emitir sempre uma sessão nova, nunca reaproveitando um identificador anterior (contra fixação de sessão). A sessão é opaca, com segredo aleatório e hash no banco.
- **Expiração:** 30 minutos sem uso ou 8 horas após o login, o que vier primeiro. A OWASP sugere inatividade de 15–30 min para aplicações de baixo risco e teto absoluto de 4–8 h para uso ao longo do dia.
- **Renovação:** `last_seen_at` é atualizado no máximo uma vez por minuto, para não gravar no banco a cada requisição.
- **Logout:** revoga no servidor e responde `Clear-Site-Data: "cookies", "storage"`. `POST /auth/logout-all` revoga todas as sessões e chaves da conta.
- **Cookie:** `__Host-gs_session`, `HttpOnly`, `Secure`, `SameSite=Lax` (`Strict` quebraria o retorno do Google e do GitHub), `Path=/`, sem Domain.

O estado transitório do login (`state`, `nonce`, `code_verifier`) não vai para o cookie de sessão do Starlette, que é assinado mas não cifrado: o Authlib é configurado com `OAuth(cache=…)`, guardando esses dados no PostgreSQL com expiração de poucos minutos, e o cookie leva só a chave. O `state` também protege o callback contra CSRF de login. [D16](GoblinShiftReferencias.md#d-4).

**CSRF.** Toda requisição autenticada por cookie que altera estado (`POST`, `PUT`, `DELETE`), inclusive `POST /auth/logout`, `POST /auth/logout-all`, `/api-keys` e `DELETE /me`, passa por três verificações:
1. **Fetch Metadata**, o sinal principal segundo a OWASP: se `Sec-Fetch-Site` vier e não for `same-origin`, recusar.
2. **Origem**, o fallback obrigatório: se `Origin` vier, deve ser exatamente a origem do serviço. `Origin` ausente ou `null` não prova nada.
3. **Token sempre exigido** (*signed double-submit*): o header `X-CSRF-Token` deve ser igual ao cookie `__Host-gs_csrf`, cujo valor é `aleatório ‖ HMAC-SHA256(segredo do servidor, id da sessão ‖ aleatório)`, conferido em tempo constante e ligado à sessão. Esse cookie não é `HttpOnly` (a SPA precisa lê-lo), é `Secure`, `SameSite=Strict`, `Path=/`, sem Domain, e é trocado a cada sessão nova.

Falha em qualquer verificação → `403` com `CSRF_FAILED`, registrado em auditoria. Nenhuma rota `GET` altera estado. A API não habilita CORS. Requisições com `Authorization: Bearer` não usam cookie e não passam pelo CSRF; enviar chave e cookie juntos continua recusado. XSS anula qualquer defesa de CSRF, daí as políticas de conteúdo abaixo. [D16](GoblinShiftReferencias.md#d-4).

**Chaves de API.** Segredo aleatório de pelo menos 256 bits com prefixo `gsk_` (para que ferramentas de *secret scanning* o reconheçam num vazamento), identificador público, hash armazenado, escopos explícitos, exibição única, validade padrão de 30 dias e máxima de 90. SHA-256 com comparação em tempo constante basta como hash, porque o segredo tem 256 bits de entropia; bcrypt e argon2 existem para senhas de baixa entropia. A tela de Acesso mostra criação, expiração e último uso de cada chave.

**Autenticação recente.** Emitir chave e excluir a conta exigem uma sessão cujo login no provedor aconteceu há no máximo 10 minutos (`authenticated_at`). Caso contrário, a API responde `401` com `REAUTH_REQUIRED`, e a SPA refaz o login com `prompt=select_account`. Uma sessão sequestrada, sozinha, não vira uma credencial de 30 a 90 dias. Limitação declarada: nenhum dos provedores deixa o cliente exigir a senha de novo (o Google não aceita `prompt=login` nem `max_age`; o GitHub só oferece `select_account`), então a reautenticação prova um login recente no provedor, não a digitação da senha. Os controles compensatórios são a validade curta, o último uso visível, a auditoria e o `logout-all`.

Chaves são enviadas em `Authorization: Bearer`, nunca na URL. API key não é ID token Google nem JWT inventado. Recusar chave e sessão simultâneas. Revogação ou bloqueio impede novas requisições; sessão e chave passam pela mesma autorização por proprietário.

**Autorização por recurso:** leitura, mutação, download e cancelamento de Upload, Run, Preset e Artifact validam o proprietário no servidor. Escopos de chave restringem ações, não autorizam recursos de outras contas. Negar por padrão. [D20](GoblinShiftReferencias.md#d-4).

**Downloads e preview.** O conteúdo do usuário é servido na mesma origem que emite chaves, então nenhum arquivo pode virar página ativa. Revalidar acesso na abertura e responder com:
- `Content-Disposition: attachment` e nome codificado (`filename*`, RFC 6266);
- `Content-Type: application/octet-stream`, exceto imagens derivadas pelo próprio serviço, servidas com o tipo real;
- `X-Content-Type-Options: nosniff` e `Content-Security-Policy: default-src 'none'; sandbox`, para que um HTML, SVG ou TXT aberto por engano não execute nada;
- `Cross-Origin-Resource-Policy: same-origin` e `Cache-Control: no-store`.

 Um stream já aberto pode terminar dentro do timeout: revogação não recolhe bytes enviados. Exclusão marca conteúdo indisponível para novos acessos; remoção física aguarda processamento/streams ativos. Não anunciar Range ou retomada sem implementação correspondente.

**Cabeçalhos da aplicação.** A SPA é servida com uma CSP própria, sem `unsafe-inline` nem `unsafe-eval` em scripts, já que o build do Vite gera arquivos estáticos (o `'wasm-unsafe-eval'` e o `blob:` em `connect-src` são do GLB da abertura, [ADR 030](adr/030-motor-e-asset-do-3d-da-abertura.md)): `default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; connect-src 'self' blob:; img-src 'self' blob: data:; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'`. Se componentes do shadcn/ui exigirem estilos inline, liberar apenas `style-src-attr 'unsafe-inline'`, confirmado na implementação. Também: `Strict-Transport-Security: max-age=63072000; includeSubDomains`, `Referrer-Policy: strict-origin-when-cross-origin`, `Cross-Origin-Opener-Policy: same-origin` e `Permissions-Policy` desligando câmera, microfone e geolocalização. [D16](GoblinShiftReferencias.md#d-4).

**Auditoria.** `AuditEvent` registra login e falha de login, logout e `logout-all`, emissão e revogação de chave, `REAUTH_REQUIRED`, `CSRF_FAILED`, negações de autorização, exclusão e bloqueio de conta, aceite dos termos e mudança de limites pelo administrador (`gs-admin set-limits`, evento `ACCOUNT_LIMITS_CHANGED`). Só esse último tem `details`: os valores antigos e novos de cada limite citado (`{"changes": {<item>: {"old", "new"}}}`, `null` nas cotas é "sem override"), números sem dado pessoal, que saem com a linha aos 90 dias. Sem nomes de arquivo, conteúdo ou segredos; o IP é truncado (/24 em IPv4, /48 em IPv6).

**Sandbox.** Três peças, com privilégios separados:
- **Worker confiável:** acessa banco e storage, prepara o diretório da tentativa e coleta os resultados. Não tem acesso ao Docker. Um worker com o socket do Docker teria, na prática, root sobre a VPS, porque quem controla o daemon monta `/` do host num container ("only trusted users should be allowed to control your Docker daemon").
- **`executor-ctl`:** serviço pequeno do host (systemd, fora do Compose), o único com acesso ao Docker. Expõe a um grupo restrito, por socket Unix, só quatro operações: `start(attempt_id)`, `status(attempt_id)`, `stop(attempt_id, grace)` e `remove(attempt_id)`. Valida o `attempt_id` como UUID, deriva sozinho os caminhos e o nome `gs-exec-{attempt_id}` e usa um perfil fixo de `docker run`: o worker não escolhe imagem, montagens nem opções. `start` é idempotente; se o container já existe, devolve o estado dele.
- **Executor:** um container novo por tentativa, com imagem fixada por digest, rodando sob gVisor (`--runtime=runsc`, kernel em espaço de usuário), `--network=none`, raiz `--read-only`, `--cap-drop=ALL`, `no-new-privileges`, usuário não-root, `--ulimit nproc` para os processos da receita, `--pids-limit` só como rede de segurança das tarefas do próprio gVisor no host (ADR 015), `--memory` (igual a `--memory-swap`) e `--cpus` com os valores de 1.7. Recebe só as entradas daquela tentativa, montadas somente leitura, e a configuração validada; sem tokens, diretórios pessoais ou credenciais. A API pública não fala com o `executor-ctl`. [D17](GoblinShiftReferencias.md#d-4), [D18](GoblinShiftReferencias.md#d-4).
  - *Origem do digest:* a CI publica `ghcr.io/bipoxdxd/goblinshift-executor` (de `docker/executor.Dockerfile`) com a mesma tag `sha-<12>` da aplicação; no deploy, `deploy/executor-image.sh` baixa essa tag, lê o digest em `RepoDigests` e o grava na allowlist `/srv/goblinshift/executor/images.json` (novo e anterior), e o `release.sh` o grava, com a tag da aplicação, em `/srv/goblinshift/secrets/release.env`, de onde o `deploy/compose.sh` o passa ao Compose como `GS_EXECUTOR_IMAGE`, tanto no deploy quanto nos comandos manuais do operador.

**Disco com limite efetivo.** O Docker só aplica `--storage-opt size` em XFS com `pquota`, e um tmpfs com teto consumiria a RAM do próprio executor. Por isso a área de trabalho (temporários + saídas) é um filesystem ext4 com o tamanho fixado em 1.7, criado como imagem e montado por loop no host em `/srv/goblinshift/exec-work`.
- Como só existe uma tentativa física por vez, o filesystem inteiro é a cota da tentativa: encher o disco falha com `ENOSPC` dentro do sandbox, sem afetar banco, storage ou sistema.
- O `executor-ctl` recusa `start` se a área não estiver vazia; ela só é limpa depois de a coleta ser persistida (3.4).
- Dentro do container, `RLIMIT_FSIZE` limita o tamanho de cada arquivo.

**Parada.** `stop` envia SIGTERM, espera a carência (até 10 s) e mata o container; `status` confirma o término antes de a execução virar `canceled` ou `failed`. Container e área de trabalho só são removidos depois da coleta, ou do descarte, persistidos no banco.

**Protocolo do `executor-ctl`.** HTTP/1.1 sobre o socket `/run/goblinshift/executor-ctl.sock` (root, grupo `gs-exec`, modo 0660), uma requisição por conexão, corpo JSON de até 1 KiB com `Content-Length` e prazo de leitura de 5 s. O `attempt_id` só vale na forma canônica do UUID (senão `400`); o único dado do cliente é a imagem, uma referência com digest conferida contra `/srv/goblinshift/executor/images.json`, relido a cada `start`. Erros: `{"error": "<código>", "detail": "..."}`.

| Operação | Requisição | Resposta |
|---|---|---|
| start | `POST /v1/attempts/{id}/start` `{"image": "<ref>@sha256:<hex>"}` | `200` Status; idempotente pelo nome. `403 image_not_allowed`, `409 work_area_dirty` / `work_dir_missing`, `503 docker_unavailable` |
| status | `GET /v1/attempts/{id}` | `200` Status |
| stop | `POST /v1/attempts/{id}/stop` `{"grace_s": 0..10}` | `200` Status após SIGTERM, carência e SIGKILL; ausente ou terminado é sucesso |
| remove | `DELETE /v1/attempts/{id}` | `204`; apaga container e `<area>/<id>`; repetir é sucesso; `409 still_running` com o container vivo |

Status: `{"state": "absent"|"created"|"running"|"exited", "exit_code", "oom_killed", "started_at", "finished_at"}`. Implementação em `deploy/executor_ctl/` (só biblioteca padrão, Python do host), instalada por `deploy/executor-ctl/install.sh` como `goblinshift-executor-ctl.service`.

A coleta ocorre após encerrar os escritores. Aceitar apenas arquivos regulares na árvore autorizada; rejeitar symlinks, hardlinks, dispositivos e caminhos externos. Limitar bytes e quantidade durante a leitura. Validar conteúdo derivado em contexto isolado quando necessário; relatório do executor também recebe validação de schema: o `code` de cada rejeição só passa se for do catálogo de 1.6, e o `summary` só com as chaves conhecidas e os valores que os próprios itens dão. Manifesto e ZIP, publicados para o dono, são conferidos contra os arquivos coletados: o manifesto lista exatamente as saídas, com tamanho e SHA-256 calculados pelo worker; o ZIP tem exatamente as saídas, o manifesto e o relatório, na ordem, cada entrada arquivo regular, não cifrado, com a compressão do empacotador e o mesmo conteúdo, sem nada descomprimido além do tamanho esperado. FFmpeg/ffprobe não recebem URLs, shell ou flags livres.

Nem container nem gVisor são garantia absoluta: o gVisor reduz muito a superfície do kernel do host exposta ao conteúdo, mas não elimina falhas. Limites declarados precisam funcionar no runtime utilizado e são provados pelo teste de aceitação do ambiente ([3.8](#s3-8)); não substituir isolamento por confiança no parser. Conteúdo de arquivo, relatório e log é dado, nunca instrução para Claude, Codex ou ferramentas de execução.

<a id="s3-7"></a>
### 3.7. Verificação técnica e diagnóstico

As verificações acompanham a funcionalidade implementada; não existe fase obrigatória de testes antes de começar o desenvolvimento nem imposição de TDD. Cobrir resultados semânticos das receitas, autorização cruzada, idempotência, queda do worker, cancelamento concorrente e exclusão de arquivos em uso. PostgreSQL, executor sob gVisor e storage reais são usados quando a afirmação depende deles; mocks ficam nas fronteiras apropriadas.

O protocolo da seção 3.4 tem testes próprios com PostgreSQL real: dois workers disputando o mesmo claim; um coordenador com geração vencida tentando escrever (toda escrita deve afetar zero linhas); publicação contra cancelamento e contra prazo vencido; e o worker morto por SIGKILL ou pausado por SIGSTOP além do lease em cada fronteira do protocolo.

A tabela de transições de 3.5 é testada como função pura com `RuleBasedStateMachine` do Hypothesis, verificando as invariantes listadas lá. O contrato é testado com Schemathesis contra o OpenAPI gerado.

Segurança web também é testada de forma automática: cada mutação por cookie é recusada com `Sec-Fetch-Site: cross-site`, com `Origin` de outra origem e sem `X-CSRF-Token`; emitir chave com login antigo devolve `REAUTH_REQUIRED`; sessão expira por inatividade e pelo teto absoluto; respostas de download e da SPA trazem os cabeçalhos exigidos em 3.6.

Pillow é conferido por dimensões, orientação e transparência; CSV por valores, ordem e contagens; Detox por bytes, nomes e mapa de duplicatas. PDF acrescenta ordem/multiplicidade de páginas; mídia acrescenta duração, faixa e instantes. Um HTTP `200` não substitui a inspeção do resultado.

**Eventos amplos.** A telemetria principal não é um log por linha nem métricas pré-agregadas, e sim um evento estruturado e largo por unidade de trabalho, com todo o contexto dela, porque "the shape of the data you collect constrains the questions you can ask later" ([B30](GoblinShiftReferencias.md#l-9), cap. 5 e 6). Cada evento principal é um span OpenTelemetry marcado com `main = true`, o que separa os eventos largos de qualquer outro log:
- um por requisição HTTP;
- um por tentativa, ligado ao trace da requisição de aceitação (ver abaixo);
- um no término de cada Run;
- um por ciclo do processo de manutenção.

| Grupo          | Atributos                                                                                                                                                                                                                                                                                                               |
|----------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Comuns         | `service.name`, `service.version` (hash do commit da imagem), `deployment.environment`, `user.hash`, `request_id`, `run_id`, `attempt_id`, `recipe_id`, `recipe_version`, `error.code`                                                                                                                                  |
| Requisição     | `http.route`, `http.request.method`, `http.response.status_code`, `auth.via` (sessão ou chave), `rate_limit.class`, `rate_limit.limited`, bytes recebidos e enviados, `duration_ms`, número e tempo de consultas ao banco, `idempotency.replayed`                                                                       |
| Tentativa      | Número da tentativa e geração; entradas e saídas (quantidade e bytes); rejeições por código; `queue_wait_ms`; duração de cada fase (`inspect_ms`, `process_ms`, `package_ms`, `collect_ms`, `publish_ms`); código de saída do executor; morte por falta de memória; `ENOSPC`; razão de parada; latência do cancelamento |
| Término da Run | Estado final, `error.code`, número de tentativas, `queue_wait_ms`, duração total, e se a execução é elegível para o SLO de execução                                                                                                                                                                                     |

- `error.code` é sempre um código do catálogo de [1.6](#s1-6), o que permite agrupar falhas por causa sem ler mensagens.
- `user.hash` é um HMAC do `owner_id` com segredo do servidor: permite agrupar por conta sem guardar o ID nem qualquer dado pessoal.
- Nunca entram em evento: conteúdo, nomes de arquivo, valores de parâmetros (filtros de CSV são dados do usuário), tokens, segredos e IP completo.
- Logs de acesso seguem a mesma regra. O Uvicorn roda sem access log, porque o evento `main` já registra cada requisição. O Nginx mantém um log de acesso próprio para o que não chega à API (413 do teto de corpo, 502 e 504 do upstream), em JSON, com o cliente truncado (/24 em IPv4, /48 em IPv6), só o caminho, sem query string, Referer nem User-Agent (`docker/nginx/templates/access-log.conf.template`, provado por `tests/test_nginx_logs.py` com canários de IP e de query).
- Sem amostragem: o volume de um serviço numa VPS cabe inteiro, e a telemetria é guardada por 30 dias, a janela dos SLOs (1.7).
- Métricas de sistema (CPU, memória, disco por filesystem) vêm do OpenTelemetry Collector no host; as de comportamento são derivadas dos próprios eventos.

[D29](GoblinShiftReferencias.md#d-6).

**Trace de ponta a ponta.** A fila no banco quebraria o contexto entre a requisição e a tentativa, então ele é guardado explicitamente: a aceitação grava na Run o `traceparent` W3C da requisição (`opentelemetry.propagate.inject`); no claim, o worker o extrai e cria o span da tentativa ligado por *span link*, não como filho, porque a espera na fila pode durar horas. O executor, sem rede, devolve no relatório os tempos de cada fase, que o worker converte em eventos do span da tentativa.

**SLOs.** Medem o que a pessoa usuária experimenta, não o estado das máquinas. São baseados em eventos (proporção de eventos bons entre os elegíveis), que o livro recomenda em vez de medidas por janela de tempo, numa janela **deslizante de 30 dias**, porque uma janela fixa zera o orçamento de forma artificial a cada mês ([B30](GoblinShiftReferencias.md#l-9), cap. 11 e 12).

| SLO       | Eventos elegíveis                                                                                                | Evento bom                                                                                                                                                                                   | Meta  |
|-----------|------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------|
| Aceitação | `POST /runs` com credencial e corpo válidos, exceto recusas de política (cota, taxa, fila cheia, execução ativa) | `202` em menos de 500 ms                                                                                                                                                                     | 99,5% |
| Execução  | Execuções que terminam, com até 10 arquivos e 50 MiB de entrada, exceto as canceladas pela pessoa usuária        | Estado terminal sem falha de infraestrutura nem de parada (`EXECUTOR_UNAVAILABLE`, `INFRA_POISON`, `DEADLINE_EXCEEDED`, `QUEUE_EXPIRED`) e em até 5 min desde o início da primeira tentativa | 99%   |
| Download  | `GET /artifacts/{id}/download` de artefato disponível e autorizado                                               | Primeiro byte em menos de 1 s e transferência concluída sem erro do servidor                                                                                                                 | 99,5% |

- Rejeições de conteúdo (arquivo inválido, `NO_VALID_OUTPUTS`) **não** gastam orçamento: o serviço funcionou, o arquivo é que não servia.
- As recusas de política ficam fora do SLO de aceitação de propósito, porque são o serviço se protegendo. Fila cheia por muito tempo é falta de capacidade e tem alerta próprio, abaixo.
- O login não tem SLO próprio: depende do Google e do GitHub, e um alerta sobre falha deles não teria ação possível.

**Política de orçamento de erro.** Com o orçamento de qualquer SLO esgotado, a próxima fatia de [3.9](#s3-9) não começa: o trabalho passa a ser estabilidade até o orçamento voltar a ficar positivo na janela deslizante. Num projeto de uma pessoa, essa é a forma concreta do "congelamento de funcionalidades" que o livro descreve.

**Alertas.** Só o que é acionável, importante e urgente ([B29](GoblinShiftReferencias.md#l-9), cap. 10). Há dois tipos:
- *Queima de orçamento* (sintoma): alerta relativo sobre janelas de 15 minutos e de 1 hora. Queima de 2× o ritmo permitido, sustentada, gera aviso; 4× ou mais gera alerta urgente. Uma janela só dispara com um mínimo de eventos elegíveis, porque, com pouco tráfego, uma falha em vinte requisições não pode acordar ninguém.
- *Causas conhecidas com ação clara*: filesystem de storage, do PostgreSQL ou da telemetria acima de 85%; backup diário que falhou; teste de aceitação do ambiente falhando; fila global cheia por mais de 15 minutos; execução presa em `stopping` por mais de 10 minutos; certificado TLS a menos de 14 dias de vencer; limitador de exclusões da manutenção acionado.

Consumo de conta perto da cota não é alerta, é informação no painel.

**Backend de telemetria: OpenObserve na VPS.** Um OpenObserve em nó único (`ZO_LOCAL_MODE`, SQLite e disco local) recebe tudo do OpenTelemetry Collector local por OTLP. Os dados não saem da VPS e não há custo. Os SLOs são calculados por alertas agendados com consulta SQL e janela de avaliação própria, e as notificações saem por e-mail ou webhook ([D29](GoblinShiftReferencias.md#d-6)). Consultas, alertas e o provisionamento ficam em `deploy/openobserve/`, com runbook próprio.
- *Retenção:* configurada explicitamente em 30 dias (por stream ou globalmente), sem depender do padrão da versão instalada, que as fontes não confirmam.
- *Disco:* filesystem próprio ([3.8](#s3-8)). Com o volume cheio, o OpenObserve para de ingerir e continua respondendo, então "telemetria acima de 85%" entra nos alertas de causa.
- *Ponto cego:* se a VPS cair, a telemetria cai junto. Um monitor de uptime externo e gratuito consulta `/readyz` e avisa por um canal independente.
- *Confirmado na fatia 0* ([ADR 013](adr/013-openobserve-telemetria.md)): com 20 000 requisições sintéticas na VPS, o OpenObserve foi de 210 a 246 MiB de RAM e guardou os 60 000 spans em cerca de 3,7 MiB; SQL e alertas agendados funcionam direto sobre o stream de traces, então os eventos `main` não precisam ser emitidos também como log. Destinos de alerta precisam ser URLs públicas ou e-mail, porque o OpenObserve recusa IPs privados (proteção contra SSRF).

Como a aplicação só fala OTLP com o Collector, trocar de backend no futuro não muda o código.

<a id="s3-8"></a>
### 3.8. Implantação na VPS

**Servidor.** VPS KVM 2 da Hostinger (2 vCPU, 8 GB de RAM, 100 GB), Ubuntu 24.04 LTS, datacenter de Campinas (SP). A mesma VPS vai hospedar também outro projeto do mesmo dono, cada um no seu domínio, o que define três regras: o GoblinShift não assume a máquina inteira, divide as portas 80/443 com o outro projeto por meio de um proxy de borda e usa usuário, diretórios e filesystems próprios. O root completo permite Docker, gVisor e filesystems dedicados. O gVisor usa por padrão a plataforma *systrap*, que dispensa virtualização aninhada e é a indicada para rodar dentro de VM.

| Recurso | Como fica no KVM 2 compartilhado                                                                                         |
|---------|--------------------------------------------------------------------------------------------------------------------------|
| CPU     | Executor limitado a 1 vCPU; o outro vCPU fica para API, worker, manutenção, PostgreSQL, Nginx e o outro projeto            |
| RAM     | Executor com até 2 GiB; a pilha do GoblinShift em repouso usou cerca de 385 MiB na VPS, na fatia 0                       |
| Disco   | 50 GiB em filesystems de tamanho fixo (abaixo); o restante fica para o sistema, imagens Docker e o outro projeto          |
| Upgrade | Para KVM 4 quando as métricas de CPU ou os SLOs pedirem; a Hostinger faz o upgrade sem reinstalar                          |

**Proxy de borda.** Só um processo pode ouvir nas portas 80 e 443, então um proxy de borda, infraestrutura do host e não de um projeto, recebe todo o tráfego e encaminha por domínio. Cada projeto conecta o seu ponto de entrada a uma rede Docker compartilhada, `edge`; o Nginx do GoblinShift não publica portas em produção. O domínio do GoblinShift é `goblinshift.dev`, o único endereço que o proxy de borda encaminha para ele: o acesso pelo IP (http) não chega ao GoblinShift, e a chave de API não trafega em texto claro. O TLS dos domínios fica no proxy de borda, um Caddy com certificados automáticos; cada projeto entrega um trecho de configuração e aponta para o IP reservado do seu ponto de entrada (ADR 014).

**Disco em compartimentos.** Filesystems separados, para que encher um não derrube os outros:
- `/srv/goblinshift/pg`: PostgreSQL, 10 GiB de tamanho fixo.
- `/srv/goblinshift/storage`: uploads e artefatos, filesystem de tamanho fixo de 24 GiB (os 20 GiB do ambiente mais folga), o que torna o teto do ambiente também um limite físico.
- `/srv/goblinshift/exec-work`: área do executor, no tamanho de 1.7 ([3.6](#s3-6)).
- `/srv/goblinshift/telemetry`: dados do OpenObserve, 10 GiB de tamanho fixo ([3.7](#s3-7)).
- Raiz: sistema, imagens Docker e logs, com rotação por serviço no Compose (até 50 MiB por serviço, [1.7](#s1-7)).

**Rede.** Firewall da Hostinger e do host (ufw) com só 80, 443 e SSH abertos, por regras acrescentadas, nunca substituindo as do outro projeto; SSH por chave, sem senha e sem login de root; fail2ban ativo. TLS no proxy de borda, com Let's Encrypt. PostgreSQL sem porta exposta. A proteção DDoS da Hostinger atua na borda; no Nginx, `limit_req` em `/auth/*`, `client_max_body_size` por rota e `proxy_request_buffering off` nos `PUT` de conteúdo, para não bufferizar arquivos inteiros em disco antes da API.

**Processos.** O projeto Compose `goblinshift`, operado pelo usuário de sistema `goblinshift`, sobe `nginx`, `api`, `worker`, `maintenance`, `postgres`, `otel-collector` e `openobserve`; o OpenObserve não tem porta exposta e é acessado só por túnel SSH. O `executor-ctl` e o gVisor são do host, gerenciados pelo systemd. As migrações do Alembic rodam num serviço de execução única antes de `api` e `worker` (`depends_on` com `service_completed_successfully`). A API é trocada sem queda: o deploy sobe a versão nova ao lado da antiga e só para a antiga, com SIGTERM, depois que a nova está saudável e o Nginx já a enxerga pelo DNS do Docker (`deploy/rollout-api.sh`). Durante um deploy, processos de versões diferentes convivem por alguns instantes, então toda mudança de schema é feita em duas etapas (*expand/contract*): primeiro só acréscimos compatíveis com o código antigo; a remoção do que ficou sem uso vem numa migração de um deploy posterior. Migração já aplicada em produção nunca é editada: o Alembic não reaplica uma revisão registrada, então a correção é sempre uma migração nova. Comandos manuais do Compose na VPS passam pelo `deploy/compose.sh`, o mesmo do deploy, que carrega a versão implantada gravada em `/srv/goblinshift/secrets/release.env`. O worker recebe um prazo de parada maior que uma tentativa para terminá-la antes de ser substituído; se o prazo estourar, o lease vence e a manutenção assume. Health checks: `/livez` e `/readyz` na API (prontidão depende só do banco) e lease renovado como sinal de vida do worker.

**Segredos.** Client secrets do Google e do GitHub, senha do banco e chaves de assinatura ficam fora do repositório, em arquivos legíveis só pelos serviços que os usam (Compose `secrets`). O repositório é público ([1.8](#s1-8)).

**Backups.** O backup semanal e os snapshots da Hostinger ficam no mesmo provedor e não bastam sozinhos (regra 3-2-1).
- PostgreSQL: dump diário cifrado enviado a um armazenamento de outro provedor, com 30 dias de retenção e teste automatizado de restauração todo mês.
- Arquivos: sem backup, por decisão, porque expiram em 48 horas. Depois de restaurar o banco, objetos ausentes ficam `missing` ([1.7](#s1-7)).
- Dados apagados por `DELETE /me` deixam de existir nos backups quando estes expiram, em até 30 dias ([1.8](#s1-8)).

**Teste de aceitação do ambiente.** Antes de abrir o serviço e a cada mudança no host, um teste automatizado prova no próprio servidor que, dentro do executor e com os limites de 1.7 (hoje 6 GiB, 2 GiB e 64 processos):
- não há rede;
- escrever além de 6 GiB falha com `ENOSPC`;
- alocar mais de 2 GiB encerra o processo por falta de memória;
- um fork bomb para em 64 processos;
- o container roda sob `runsc`;
- e que o worker não consegue abrir `/var/run/docker.sock`.

[D17](GoblinShiftReferencias.md#d-4), [D30](GoblinShiftReferencias.md#d-6).

<a id="s3-9"></a>
### 3.9. Ordem de construção em fatias

Cada fatia funciona de ponta a ponta, vai para a VPS pelo pipeline e só então a próxima começa. Os maiores riscos vêm primeiro: *Pragmatic Programmer*, Topic 12, manda priorizar "the areas where you have doubts, and where you see the biggest risks" ([B01](GoblinShiftReferencias.md#l-1)); *Code Complete*, cap. 29, alerta que "if none of it will work until all of it works, it can seem as though it will never be finished" ([B02](GoblinShiftReferencias.md#l-1)); Brikman recomenda adotar a entrega contínua aos poucos, com cada passo valendo por si ([B29](GoblinShiftReferencias.md#l-9)). O serviço só abre ao público na fatia 8; antes disso, o acesso é por chave de API emitida pelo administrador.

| Fatia | Entrega                                 | Risco que elimina                                                       |
|-------|-----------------------------------------|-------------------------------------------------------------------------|
| 0     | Fundação e deploy na VPS                | Deploy deixado para o fim; "funciona na minha máquina"                  |
| 1     | Protótipo descartável do sandbox        | A arquitetura inteira depender de um isolamento que não funciona na VPS |
| 2     | CSV Janitor como biblioteca pura        | Regras semânticas mais ricas, testadas sem infraestrutura               |
| 3     | Fluxo durável de ponta a ponta          | Protocolo de fila, fencing e publicação                                 |
| 4     | Executor real (gVisor + `executor-ctl`) | Isolamento, parada e recuperação em produção                            |
| 5     | Image Forge e Downloads Detox           | Pillow com arquivos reais; pacote e manifesto comuns                    |
| 6     | Identidade e segurança web              | Contas abertas, sessão, CSRF, chaves e cotas                            |
| 7     | Interface React                         | Uso real por pessoas, acessibilidade                                    |
| 8     | Abertura pública                        | Operar um serviço aberto: backups, alertas, termos, abuso               |
| 9     | PDF Press                               | Parse de PDF hostil no executor; composição incompleta tida como sucesso |
| 10    | Media Mill                              | Decodificar vídeo, a maior superfície de ataque; FFmpeg abrindo outros arquivos |

**Regras de todas as fatias:**
- Todo PR traz os testes das regras que altera, e o CI precisa estar verde para o merge ([B22](GoblinShiftReferencias.md#l-7): "Even if I do not use TDD, I always write tests promptly").
- Toda decisão grande vira um ADR curto, com alternativas e motivo.
- Uma fatia está pronta quando o critério abaixo passa **na VPS**, não só localmente.

**Fatia 0. Fundação e deploy.**
- *Entra:* repositório público, uv, Ruff, mypy, pytest, CI, Compose com `nginx`, `api`, `postgres`, `otel-collector` e `openobserve`, Alembic como serviço de execução única, `/livez` e `/readyz`. Na VPS: Ubuntu LTS, firewall, SSH só por chave, TLS, segredos fora do repositório e os filesystems de [3.8](#s3-8).
- *Pronto quando:* um push na `main` passa pelo CI, publica as imagens e faz o deploy; `https://…/readyz` responde `200`; e a medição do OpenObserve (RAM, disco, SQL sobre traces) fecha o ADR 013.

**Fatia 1. Protótipo descartável do sandbox.**
- *Entra:* gVisor instalado na VPS, um `executor-ctl` mínimo e o teste de aceitação do ambiente de [3.8](#s3-8). É código para jogar fora, o mínimo necessário para responder a uma pergunta de design (*Code Complete*, cap. 5).
- *Pronto quando:* o teste de aceitação passa na VPS: sem rede, `ENOSPC` aos 6 GiB, morte por falta de memória acima de 2 GiB, fork bomb contido em 64 processos, container sob `runsc` e o worker sem acesso a `docker.sock`. Se algo falhar, a alternativa (outro plano de VPS, outro runtime) é decidida e registrada num ADR antes de seguir.

**Fatia 2. CSV Janitor como biblioteca pura.**
- *Entra:* a receita como função pura, só com a biblioteca padrão, e uma CLI. Testes de especificação e de fronteira (64 KiB exato e +1 byte em UTF-8 multibyte, 20.000 e 20.001 registros), propriedades com Hypothesis (a equação de contagem, ida e volta de parse/serialize, `00123` preservado, deduplicação idempotente) e os casos que o módulo `csv` não detecta sozinho (aspas no meio do campo, número de campos, limite em bytes).
- *Fora:* API, banco e fila.
- *Pronto quando:* a CLI processa a suíte de fixtures e cada regra de [1.3](#s1-3) tem pelo menos um teste.

**Fatia 3. Fluxo durável de ponta a ponta.**
- *Entra:* `POST /uploads` em lote, `PUT` de conteúdo com hash e tamanho contados no streaming, `POST /runs` com idempotência e a decodificação JSON protegida, `GET /runs`, resultados e download, erros em problem+json. A fila no PostgreSQL com claim, `Attempt`, geração e fencing, a publicação condicional e o job de retenção. O worker roda a receita por uma interface `Runner` com uma implementação local em subprocesso, permitida na VPS só até a fatia 4 e com confirmação explícita ([ADR 016](adr/016-runner-local-na-vps-ate-a-fatia-4.md)). Uma chave de API emitida por CLI do administrador (`gs-admin`).
- *Fora:* login, sandbox real, interface e as cotas completas (`CapacityPool`, `ResourceReservation`, cotas diárias), que ficam para a fatia 6 por decisão do usuário. Na fatia 3, a fila global de 10 aguardando é serializada por `pg_advisory_xact_lock` e a execução ativa por conta é garantida pelo índice único parcial `run_one_active_per_owner_idx`.
- *Pronto quando:* um script com `curl` executa o CSV Janitor de ponta a ponta na VPS, e passam os testes de concorrência com PostgreSQL real: dois workers disputando o mesmo claim, escrita com geração vencida afetando zero linhas, publicação contra cancelamento e contra prazo vencido.

**Fatia 4. Executor real.**
- *Entra:* `executor-ctl` de produção, um container gVisor por tentativa, a área de trabalho de tamanho fixo, a coleta com rejeição de symlinks e hardlinks, a parada, o lease, o processo `maintenance` (reconciliação, `QUEUE_EXPIRED`, prazo vencido) e os prazos de [1.7](#s1-7). Em produção, o serviço recusa iniciar com o `Runner` local.
- *Pronto quando:* o teste de aceitação continua passando, e os testes de caos passam na VPS: worker morto por SIGKILL e pausado por SIGSTOP além do lease em cada fronteira do protocolo, sem publicação duplicada nem container órfão.
- *Confirmado na fatia 4* (VPS, 2026-10-14): o teste de aceitação passou 10/10, com `ENOSPC` depois de 5,80 GiB em 45 s, OOM com o último bloco em 1984 MiB, fork bomb parado em 63 processos e o container sob `runsc` (kernel `4.19.0-gvisor`). O teste de caos passou 49/49 critérios em 8 min 59 s: SIGKILL e SIGSTOP além do lease em cada fronteira, publicação única, nenhum container órfão e download igual byte a byte. O e2e do CSV Janitor passou pelo `executor-ctl`. Incidente no caminho: a migração 0002 tinha sido editada depois de aplicada na VPS, e o Alembic não a reaplicou, então a coluna `artifact.file_name` faltava em produção e toda publicação falhava; a 0004 reconciliou o schema (PR #9). Daí a regra de [3.8](#s3-8): migração aplicada nunca é editada.

**Fatia 5. Image Forge e Downloads Detox.**
- *Entra:* as regras de [1.2](#s1-2), com a validação de pixels antes de `load()`, e de [1.4](#s1-4); empacotador e manifesto comuns às receitas (as regras de nomes de [3.6](#s3-6) já valem desde a fatia 3).
- *Inclui:* fotos de celular, com MPO pelo quadro principal e Display P3 convertido para sRGB ([1.2](#s1-2)).
- *Pronto quando:* passa a suíte de fixtures com MPO, Display P3, PNG de 16 bits, bomba de descompressão, ZIP que na verdade é JAR e nomes em colisão, e o ZIP gerado é determinístico.
- *Confirmado na fatia 5* (PR #14, 2026-10-14; robustez do worker no #17 e do teste de caos no #18): a suíte de fixtures passa no CI, inclusive os testes da imagem do executor (marca `executor_image`, que também roda sob `runsc` no job `gvisor`), com MPO pelo quadro principal, Display P3 com orientação 6, PNG de 16 bits, bomba de descompressão, ZIP que é JAR e nomes em colisão; cada receita roda duas vezes e o ZIP sai com o mesmo SHA-256. Na VPS, sob gVisor (1 vCPU, 2 GiB, sem rede), 100 imagens de 24 MP levaram 193 s, contra o teto de 600 s da tentativa (medição registrada na revisão do #14 e no [ADR 018](adr/018-pillow-na-imagem-do-executor.md)). O teste de caos na VPS passou 49/49 depois do #18, que fez a regra de publicação única contar também o manifesto e o pacote (antes dele, 43/49). Sem registro no repositório: a rodada dos `scripts/e2e-*.sh` contra a VPS depois do deploy.

**Fatia 6. Identidade e segurança web.**
- *Entra:* login Google (OIDC) e GitHub (OAuth 2.0), sessão, CSRF, chaves com escopo e autenticação recente, `logout-all`, auditoria, cabeçalhos de [3.6](#s3-6), estado da conta e bloqueio por CLI, cotas completas (`CapacityPool`, `ResourceReservation`, cotas diárias) e `DELETE /me` ([1.8](#s1-8)).
- *Pronto quando:* passam os testes de segurança de [3.7](#s3-7) e a matriz de autorização (toda rota × dono, outra conta e escopo ausente); registrar uma rota fora da matriz faz o teste falhar.
- *Confirmado na fatia 6* (PR #20, 2026-10-15; com #27, #26 (limite de requisições, [ADR 028](adr/028-limite-de-requisicoes-no-postgresql.md)) e #33 (aceite dos termos, [ADR 029](adr/029-aceite-versionado-dos-termos.md)), os três de 2026-10-14; a SPA no #37 e o contrato no #38, ambos de 2026-10-15): a matriz de autorização (`tests/security/test_authz_matrix.py`) percorre as rotas registradas no FastAPI, e `test_toda_rota_registrada_esta_na_matriz` reprova a rota que não esteja nela. Ela confere status e `problem+json` sem dado da conta para credencial ausente (401), escopo ausente (403 `INSUFFICIENT_SCOPE`, auditado como `ACCESS_DENIED`), chave em rota de sessão (403) e a allowlist pública. `tests/api/test_csrf.py`, `tests/api/test_security_headers.py`, `tests/test_nginx_hsts.py`, `tests/test_nginx_auth_limit.py` (Nginx real) e o canário de `code`, `state` e cookie nos spans do `/auth/callback` cobrem o resto de 3.7, e o Schemathesis (`--checks=all`) roda no CI. A revisão do alerta `authorization-logic` achou e corrigiu um defeito: `unblock_account` reativava conta com exclusão pedida. Está tudo na `main` e passa no CI a cada PR (último CI verde da `main` em 2026-10-16); o deploy da CI implantou a fatia na VPS, mas o login com os provedores reais não tem ensaio registrado no repositório.

**Fatia 7. Interface React.**
- *Entra:* primeiro a Bancada (upload por XHR com progresso real, formulário por receita tipado pelo OpenAPI, polling respeitando `Retry-After`), depois Execução/resultado, Histórico e Acesso. A oficina com goblins entra por último: "Start with a feature, not a layout" ([B28](GoblinShiftReferencias.md#l-8)).
- *Pronto quando:* uma ou duas jornadas Playwright (upload → execução → download) passam contra a VPS, com navegação por teclado e alternativa ao arrasto para reordenar entradas.
- *Confirmado na fatia 7, em parte* (infraestrutura no #21, telas no #22, #24, #29 e #30, Playwright no #31, todos de 2026-10-14; Entrar, Acesso e termos no #37, de 2026-10-15): as jornadas das receitas (`web/e2e/specs/journeys.spec.ts`: arquivos → reordenar por botões → parâmetros → executar → baixar) passam no job "Interface no navegador (Playwright)" do CI, em desktop de 1280 px e celular de 400 px, com a ordem do Tab e o anel de foco (`focus.spec.ts`), axe e contraste nos dois temas. A alternativa ao arrasto são os botões Mover para cima/baixo do `ReorderableList`. Esse CI usa uma API falsa dentro do processo do Playwright. Contra a VPS roda, depois de cada deploy, o job "Jornadas na VPS (pós-deploy)" (PR #79, 2026-10-16): `scripts/check-spa-edge.sh` (16 verificações de cabeçalhos, cache e tipos) e as jornadas de **leitura** (Oficina, Entrar, Termos, Privacidade, Créditos, Bancada, 404, `/docs` e `/openapi.json`) em Chromium e Firefox, com axe e CSP; passou na `main` em 2026-10-16.
- *Pendente na fatia 7:* a jornada **com escrita** (upload → execução → download) contra a VPS. O modo `E2E_VPS` só faz `GET` por desenho (um fixture reprova qualquer outro método, #79), e as jornadas com conta (Bancada com o catálogo, Histórico, Acesso) são puladas porque o secret `E2E_VPS_API_KEY` não existe no repositório nem no environment `production` (conferido com `gh secret list` em 2026-10-16). Falta decisão e credencial do usuário: uma conta de teste que já aceitou os termos e uma chave com escopo de escrita em produção. O aceite dos termos só sai por sessão, então também falta um caminho no `gs-admin` para aceitá-lo (registrado como fora do #37).

**Fatia 8. Abertura pública.**
- *Entra:* termos e aviso de privacidade revisados juridicamente, backup externo do PostgreSQL com restauração testada, eventos amplos, SLOs e alertas de [3.7](#s3-7) no OpenObserve, monitor de uptime externo, um teste de carga simples que confirma os limites por classe de rota, e um roteiro de resposta a abuso (como bloquear uma conta e o que verificar).
- *Pronto quando:* a primeira restauração de backup funcionou, os três SLOs aparecem calculados sobre tráfego real, um alerta de queima e um de causa dispararam num teste provocado, e o roteiro de abuso foi executado uma vez de ponta a ponta.
- *Confirmado na fatia 8, em parte:*
  - **Backup e restauração** ([ADR 022](adr/022-backup-externo.md); PRs #16, #56, #61, #63): o workflow "Restauração testada do backup" restaurou na CI o dump mais recente do bucket do B2 (dump de 2026-10-16 00:57 UTC) num PostgreSQL efêmero e terminou com `restauração conferida: revisão 0009, dump de 0 h` (run 38834729030, 2026-10-16 01:22 UTC). Duas falhas vieram antes e foram corrigidas: o B2 com Object Lock recusava o `PUT` sem `Content-MD5` (#61) e o `dump_check.py` usava sintaxe do Python 3.14 num runner 3.12 (#63).
  - **Evento amplo, SLOs e alertas como código** (#16, 2026-10-14; alertas de causa no #81): o SQL dos SLIs, a queima em duas janelas, as causas e o heartbeat têm testes contra o OpenObserve real no job "SLOs e alertas (OpenObserve)" do CI.
  - **Limites e carga** (#26, #27 e #32, 2026-10-14): o limite por conta e classe de rota e o `limit_req` de `/auth/*` têm testes de integração e de Nginx real; o k6 contra o Compose local passou nos cenários de fila (10 aceitas e 2 `503 QUEUE_FULL`), transferências (`429` na terceira) e jornada (58/58 execuções, `POST /runs` p99,5 de 369 ms), com os limiares dos SLOs. A VPS ficou de fora de propósito, por ser compartilhada com o StillOn.
  - **Roteiro de abuso** (#44, 2026-10-15): `docs/runbooks/abuso.md`, `gs-admin find-user --user-hash` e um teste de ponta a ponta pela API e pelo login (`tests/api/auth/test_block_end_to_end.py`: depois do `gs-admin block`, sessão e chave dão 401, a execução vira `canceled`, o login é recusado e a auditoria registra `ACCOUNT_BLOCKED`).
  - **Aceite dos termos** (#33, [ADR 029](adr/029-aceite-versionado-dos-termos.md)), **aceitação agendada** com ping no healthchecks.io (#47, criada como diária e hoje semanal, aos domingos às 07:30 UTC) e **vigia do certificado TLS** (#57, executado todo dia pela CI).
- *Pendente na fatia 8:* o serviço continua fechado ([ADR 023](adr/023-cadastro-fechado-ate-a-abertura.md)), e estes critérios ainda não foram cumpridos:
  - **Revisão jurídica dos termos e do aviso de privacidade.** Os textos estão em `v3-rascunho` e marcados "não publicar sem revisão jurídica humana" (`docs/legal/notas-internas.md`). Depende do advogado que o usuário contratar.
  - **Três SLOs sobre tráfego real.** Com o cadastro fechado (`GS_SIGNUP_MODE=closed`) não há tráfego de terceiros; abrir é decisão do usuário e vem depois da revisão jurídica.
  - **Alertas de queima e de causa disparados num teste provocado na VPS.** Não há registro de que o provisionamento (`python -m openobserve provision`, por túnel SSH) tenha rodado nem de que o disparo tenha sido feito. Faltam o webhook do canal urgente (`GS_ALERT_URGENT_URL`), uma credencial do usuário, e o acesso SSH dele.
  - **Monitor de uptime externo de `/readyz`** (UptimeRobot ou Better Stack): é uma conta do usuário e não há registro de que exista. O vigia do certificado TLS e os pings do healthchecks.io cobrem outros riscos.
  - **Cenários `limite-por-conta` e `limite-auth` do k6 contra a VPS.** Confirmados localmente no PR #102 (ainda aberto), rodada de 2026-10-16 no commit `a6025be`: 190/190 requisições dentro do limite passaram e 3/3 acima dele foram recusadas com `429` e `Retry-After`; no login, 11/11 passaram e a 12ª recebeu `429` do Nginx. Contra a VPS segue pendente de decisão do usuário, porque a máquina é dividida com o StillOn.
  - **Ensaio do roteiro de abuso na VPS.** O teste automatizado prova o comando, mas o próprio PR #44 deixa o ensaio na VPS como passo humano: exige o usuário (acesso com `sudo`) e uma conta de teste.

**Fatia 9. PDF Press.** Receita do produto desde 2026-10-16 (escopo completo; antes, "extensão"), com [ADR 035](adr/035-pdf-press-com-pikepdf.md).
- *Entra:* as regras de PDF de [1.5](#s1-5): `merge`, `split` e `select` com pikepdf/qpdf só no executor, a execução inteira falha diante de PDF danificado ou com recurso não suportado, e o mapa de páginas. Na interface, a seleção em cartões numerados, reordenáveis por botões.
- *Pronto quando:* as fixtures hostis (ciclo na árvore de páginas, `/Count` e `/Size` mentirosos, `/Type` divergente de `/Kids`, JavaScript, criptografia) são recusadas com o código certo dentro dos tetos do executor; o ZIP é determinístico; e os testes da imagem passam sob `runsc`.
- *Confirmado na fatia 9, em parte* (PR #78, 2026-10-16): as condições acima passam no CI, inclusive o job `gvisor` e o Schemathesis. *Pendente:* `scripts/e2e-pdf-press.sh` contra `https://goblinshift.dev`, que o PR indica para depois do deploy e pede uma chave de API do usuário; não há registro de que tenha rodado.

**Fatia 10. Media Mill.** Receita do produto desde 2026-10-16, com ADR 036; no PR #74, ainda aberto.
- *Entra:* as regras de mídia de [1.5](#s1-5): FFmpeg mínimo compilado na imagem do executor (LGPL, só os demuxers `mov` e `matroska` e os protocolos `file` e `pipe`), assinatura conferida antes do ffprobe, allowlist de codecs e tetos de tempo por chamada.
- *Pronto quando:* as fixtures hostis (HLS disfarçado de `.mp4`, `concat`, codec fora da allowlist, 4K, 120 fps, 96 kHz, 301 s, truncado) são recusadas; a imagem do executor mostra só o que o ADR 036 permite; e o pior caso (5 vídeos de 300 s em 1080p60) cabe nos 10 minutos da tentativa **na VPS**, sob gVisor e 1 vCPU, com `sudo python3 -B -m acceptance.media_mill_load`.
- *Pendente na fatia 10:* a revisão do usuário no PR #74 e a medição do pior caso na VPS (referência local de 95 s, sem gVisor). A medição pede o `sudo` do usuário na VPS, e o ADR 036 manda reduzir os tetos ou o `max_inputs` antes do merge se não couber.

Um backend de storage compatível com S3 continua fora do plano e não é uma receita: só entra se aparecer o gatilho de [2.1](#s2-1), mais de um servidor ou falta de disco.

<a id="s3-10"></a>
### 3.10. Decisões registradas

Cada linha vira um ADR curto no repositório (`docs/adr/`), com contexto, alternativas, consequências e como verificar o cumprimento. Assim, a mesma discussão não é refeita a cada mês (o antipadrão *Groundhog Day*, [B14](GoblinShiftReferencias.md#l-4), cap. 21).

| ADR | Decisão                                                        | Alternativas descartadas                                                   | Motivo principal                                                                                                                                                   |
|-----|----------------------------------------------------------------|----------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 001 | Monólito modular, com processos `api`, `worker`, `maintenance` | Microserviços                                                              | Um desenvolvedor, um banco, um servidor; a separação que importa é de processos e de privilégios                                                                   |
| 002 | PostgreSQL como fonte de verdade e como fila                   | RabbitMQ + Celery; Redis                                                   | Aceitar e enfileirar na mesma transação, sem dupla escrita nem outbox ([3.1](#s3-1))                                                                               |
| 003 | Sem Redis                                                      | Redis para fila, cache, sessões ou rate limiting                           | Nenhum desses papéis compensa um armazenamento a mais com uma VPS e uma tentativa por vez                                                                          |
| 004 | Um container gVisor por tentativa, criado pelo `executor-ctl`  | Socket do Docker no worker; serviço executor fixo com subprocessos; nsjail | Isolamento forte e estado novo por tentativa, sem dar root do host ao worker ([3.6](#s3-6))                                                                        |
| 005 | Área do executor em filesystem de tamanho fixo                 | `--storage-opt` (exige XFS + pquota); tmpfs                                | Cota efetiva sem consumir RAM, aproveitando a tentativa única                                                                                                      |
| 006 | Agregado Run e a transação multiagregado da publicação         | Publicar saídas e estado em transações separadas                           | Publicação parcial é pior que acoplar dois agregados numa transação curta                                                                                          |
| 007 | `StoredObject` e exclusão sempre adiada                        | Associação polimórfica em Artifact; `409` ao excluir arquivo em uso        | FK real, reaproveitamento seguro pelo Detox e nenhum erro de "em uso"                                                                                              |
| 008 | Google e GitHub, sem vínculo automático de contas              | Vincular por e-mail; senhas próprias                                       | E-mail não prova identidade entre provedores; não gerenciar senhas                                                                                                 |
| 009 | Filesystem local, sem backend S3 na v1                         | S3 desde o início                                                          | Generalidade especulativa enquanto houver um servidor                                                                                                              |
| 010 | Python 3.14                                                    | 3.13 (só segurança); 3.15 (recém-lançada)                                  | Linha com correções de bugs e ecossistema maduro                                                                                                                   |
| 011 | VPS KVM 2 da Hostinger, compartilhada com outro projeto do mesmo dono | KVM 4 dedicado; uma VPS por projeto | Custo de portfólio; os recursos medidos cabem com o executor em 1 vCPU. Isolamento entre projetos depende do gVisor, risco aceito; upgrade para KVM 4 quando as métricas pedirem |
| 012 | Serviço aberto a qualquer conta Google ou GitHub, com cotas    | Allowlist ou convite                                                       | Decisão de produto (portfólio e serviço aberto); o abuso é contido por cotas e limites ([1.8](#s1-8))                                                              |
| 013 | OpenObserve auto-hospedado como backend de telemetria          | Honeycomb gratuito; Grafana Cloud gratuito; SigNoz                         | Leve, SQL sobre os eventos, dados na VPS e sem custo; Grafana Cloud gratuito guarda só 14 dias e o SigNoz pede 4–8 GB de RAM. Confirmado pela medição da fatia 0 |
| 014 | Proxy de borda do host, compartilhado pelos projetos, com TLS por domínio | Cada projeto publicar a própria porta; o Nginx do GoblinShift atender também o outro projeto | Só um processo ouve na 80/443; manter a borda fora dos projetos evita que um deploy derrube o outro. Caddy, com TLS automático, um trecho por projeto em `conf.d` e destino por IP reservado (escolhido com o primeiro domínio, 2026-10-13) |
| 015 | Limite de processos sob gVisor: `--ulimit nproc=64` na receita e `--pids-limit=256` como rede de segurança | Só `--pids-limit` (64 ou calibrado em ~160) | O `--pids-limit` conta as tarefas do gVisor no host (31 ociosas, ~2 por processo) e mata o sandbox; o RLIMIT_NPROC dá `EAGAIN` no 64º processo. Medido na fatia 1 |
| 016 | *Substituído na fatia 4.* Runner local em subprocesso na VPS até a fatia 4, só com confirmação explícita; serviço fechado (chaves do `gs-admin`) | Fatia 3 pronta só localmente                                               | O critério de 3.9 exige `curl` na VPS e o gVisor real só chega na fatia 4. Na fatia 4, produção só aceita o `executor-ctl` e a confirmação `GS_ALLOW_LOCAL_RUNNER` foi removida |
| 017 | O teste de aceitação suspende a manutenção (`docker stop`) durante as sondas | Carência na manutenção para tentativa desconhecida; prefixo de nome ignorado pela limpeza | Não muda produção por causa de um teste, e a sonda continua idêntica a uma tentativa real |
| 018 | Pillow 12.3.0 fixado na imagem do executor, instalado do `uv.lock` por um estágio de build | `requirements.txt` próprio com hashes; `python3-pil` do Debian | Uma fonte da versão, hashes conferidos e nem `pip` nem `uv` na imagem final; só o executor carrega o PIL |
| 019 | `out/` com um nível de grupos de lista fechada; pacote `<recipe_id>.zip`; papéis `manifest` e `package` | `out/` plano com grupo só no ZIP; árvore livre | Mesmo caminho no ZIP e no download, coleta simples e segura |
| 020 | O Detox copia os bytes para `out/`; o worker reaproveita o objeto da entrada quando hash e tamanho batem | `OutputDir.reference` com campo no relatório; sempre copiar | Contrato do executor intacto, o worker confere o próprio hash e renomear não duplica objetos (1.4) |
| 021 | Digest da imagem no manifesto por um `context.json` opcional | Chave nova obrigatória no `job.json`; digest só nos metadados | Compatível nas duas direções durante o deploy (*expand/contract*) |
| 022 | Backup externo do PostgreSQL por timer do systemd, cifrado com `age`, no Backblaze B2 com Object Lock, e restauração mensal no GitHub Actions | Serviço no Compose com cron; ciclo no `maintenance`; CI puxando o dump por SSH; `rclone`; `gpg` | Independe da saúde da aplicação; dump já sai cifrado; versões imutáveis por 30 dias |
| 023 | Cadastro fechado até a abertura pública: identidade nova nasce bloqueada e o admin a libera (`gs-admin unblock`) | Allowlist de `(issuer, subject)`; cadastro aberto na fatia 6 | O serviço só abre na fatia 8; reaproveita o estado `blocked` e abrir é trocar `GS_SIGNUP_MODE` |
| 024 | Livro-razão de capacidade no módulo `capacity`, abaixo de `files` e `runs`; fila e execução ativa ficam nas travas atuais | Livro-razão em `files` ou em `runs`; fila no pool | Reservas de upload e de Run no mesmo livro-razão sem ciclo entre módulos; não trocar mecanismos já testados |
| 025 | Authlib ≥ 1.6.12 com estado do login no PostgreSQL, sessão server-side, `redirect_uri` de `GS_PUBLIC_ORIGIN`, CSRF e cabeçalhos com código próprio em ASGI puro | `httpx-oauth`; autenticação à mão; sessão em cookie assinado | Não construir autenticação; revogação imediata; o streaming dos downloads não quebra |
| 026 | Auditoria sem FK para a conta, mantida 90 dias depois do `DELETE /me` | Apagar os eventos com a conta; HMAC no lugar do id | O rastro de abuso não some com a conta; sem `app_user`, o UUID não identifica ninguém |
| 027 | Cena 3D interativa na abertura da Oficina, num chunk próprio carregado depois do `load` e de um momento ocioso, com imagem estática no lugar até lá e tetos de peso conferidos no build | 3D só como fonte de renders 2D; "Ver em 3D" sob demanda | Decisão do usuário, contra a recomendação: impacto visual na primeira tela sem atrasar o conteúdo nem a ação |
| 028 | Limite de requisições por conta e classe de rota em janela fixa de um minuto no PostgreSQL, com `INSERT … ON CONFLICT DO UPDATE … WHERE` atômico e contado na autenticação | Contador em memória do processo; `limit_req` do Nginx; janela deslizante; Redis | Vale entre réplicas e pela conta, não pelo IP, sem armazenamento novo; nenhuma rota autenticada escapa |
| 029 | Aceite versionado dos termos nas colunas da conta, com evento `TERMS_ACCEPTED`, conferido em `authenticate` (`403 TERMS_NOT_ACCEPTED`) e aceito só pela sessão web | Tabela de histórico de aceites; dependência por rota; checar só no callback do login | Rota nova já nasce bloqueada e chaves e sessões antigas também são cobertas, sem tabela nova |
| 030 | Cena 3D da abertura com three.js mínimo (162 KB gzip) e só a Rúbia em GLB de 3,36 MB com compressão Meshopt; as outras goblins são imagens 2D nos estados; CSP com `'wasm-unsafe-eval'` e `blob:`. Substitui em parte o 027 (teto do GLB e CSP) | `<model-viewer>`; vídeo ou imagem pré-renderizada; decimação para 30 mil triângulos | Sem URL de terceiros e com controle do movimento e do descarte; a decimação deixava pintas nas costuras das UVs do Rodin, e o Meshopt mantém a Rúbia igual à bruta nos closes. Os tetos de peso são conferidos no CI |
| 031 | Aviso global em `web/src/notice/notice.json`, validado por Zod e embutido no bundle; publicar é um PR | `/aviso.json` servido pelo Nginx a partir de um volume da VPS | Revisão e histórico no git, sem pedido extra nem layout shift; o custo são 10 a 15 min de deploy, aceitável para os avisos que os termos prometem |
| 032 | Paleta "Oficina de taverna": madeira, cobre (primária), âmbar (foco e aviso), brasa e pátina; substitui musgo e latão | Pergaminho; Lareira; manter musgo e latão | Verde só nas goblins e cobre como única cor do que se clica; contrastes medidos pelo `contrast.test.ts` sem falha. Decisão do usuário entre três propostas |
| 033 | Segredos de deploy só no environment `production`, restrito à `main` | Segredos no repositório; required reviewers | Workflow em outro branch ou PR não lê a chave da VPS; aprovação manual de dev solo só atrasa. A política e a posição dos segredos são configuração do GitHub, que o código não garante |
| 034 | "main = root" na VPS compartilhada aceito por escrito, com fitness function do Compose de produção e CODEOWNERS em `deploy/`, `compose*.yaml` e workflows | Deploy por wrapper root com `sudo` restrito e fora do grupo `docker`; Docker rootless; `sudo` para script do checkout | A alternativa B tira a lógica de deploy do repositório e a C quebra a rede `edge` e o `executor-ctl`; o script do checkout não protege. Gatilhos para revisar registrados. Decisão do usuário em 2026-10-15 |
| 035 | PDF Press com pikepdf/qpdf só no executor, falha da execução inteira, `upload_id` pelo `context.json` e mapa de páginas como conteúdo | Rejeição por item; chave nova no `job.json`; sanitizar em vez de recusar; grupo `documents/` | 1.5 não aceita composição incompleta; compatível com executores anteriores; recusar o detectado sem prometer sanitização |
| 036 | *Em revisão (PR #74).* FFmpeg 8.1.2 mínimo compilado na imagem do executor (LGPL, só os demuxers `mov` e `matroska`), assinatura conferida antes do ffprobe, flags fixas e tetos de tempo por chamada | `ffmpeg` do Debian; build estático de terceiros fixado por digest; imagem própria no GHCR; teto que falha a execução inteira | HLS, `concat` e afins nem existem no binário, a confiança fica na cadeia de build própria e um vídeo ruim recusa só a si mesmo. Pior caso a medir na VPS antes do merge |
| 037 | *Em revisão (PR #91).* Previews no grupo `previews/`, com papel `preview` e `source_artifact_id` apontando a saída de origem | Preview como saída comum; gerado pela API ou pelo worker; gravado junto de cada saída; FK `RESTRICT` | Fora do pacote e do manifesto, decodificação só no executor (ADR 018), opcionais e gravados por último, sem estourar o teto de 220 arquivos |
| 038 | *Em revisão (PR #94).* Elenco em 3D num palco só, uma goblin por vez, GLB da escolhida sob demanda e pose própria (giro livre e inclinação de -15° a +35°). Substitui o item 4 do 030 | Cinco goblins ao mesmo tempo; cinco palcos; `OrbitControls` | A abertura continua baixando só a Rúbia, um único contexto WebGL, o arrasto vertical com um dedo continua rolando a página e o teclado chega ao modelo |

[↑ Sumário](#sumario)

---

> **Natureza do documento:** especificação de comportamento e arquitetura. Compatibilidade instalada, segurança efetiva e resultados de processamento são verificados na implementação, não presumidos a partir desta redação.
