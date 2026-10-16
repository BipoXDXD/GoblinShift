# Arte do site: origem, direitos e licença

As goblins do site e a marca são **ilustrações e modelos 3D gerados com processamento externo e editados**, com exceção do ícone da aba, desenhado à mão. Este arquivo registra de onde vieram, com base em quais termos estão no site e sob qual licença está a parte humana do trabalho. O registro arquivo a arquivo, com sha256 e a cadeia de gerações, fica em [`web/src/assets/art/PROVENANCE.json`](../../web/src/assets/art/PROVENANCE.json), conferido no CI por `web/scripts/check-provenance.ts` ([ADR 030](../adr/030-motor-e-asset-do-3d-da-abertura.md)).

## O que está no site

| Goblin | Onde | Modelo 3D (fonte externa fonte externa) |
|---|---|---|
| Rúbia | Cena 3D da abertura e a imagem estática dela; estado carregando, em rotação com a Fagulha; créditos | `9f030f3c` (regeração de 1 milhão de triângulos da `e1c79f67`, mesma imagem de referência) |
| Nyx | Estado de erro; créditos | `19a1ddba` |
| Ferrugem | Estado de sucesso; créditos | `ec3a728c` |
| Jade | Estado vazio; créditos | `3c7aa397` |
| Fagulha | Estado carregando, em rotação com a Rúbia; créditos | `0d84776f` |

Cadeia da Rúbia, da Nyx e da Ferrugem (detalhe no `PROVENANCE.json`):

1. Conceito 2D **só por texto** no fonte externa (ByteDance), pela API da fonte externa.
2. Versão frontal e versão semi-realista por image-to-image (fonte externa e fonte externa, do Google, pela fonte externa), tendo como entradas **só imagens nossas**: o conceito do passo 1 e renders nossos de modelos do fonte externa.
3. Modelo 3D no fonte externa fonte externa Gen-2.5 a partir da imagem do passo 2.
4. Edição humana: render no Blender, recorte e conversão (`scripts/arte/gerar-imagens.sh`); simplificação da malha, redimensionamento de textura e compressão do GLB (`scripts/arte/otimizar-glb.sh`).

A Ada (fonte externa `417f7fe0`) saiu do site em 2026-10-15, por decisão do usuário; ela fica em `excluded` no `PROVENANCE.json`.

## A marca

Escolhida pelo usuário em 2026-10-15, na rodada r1 da logo:

| Peça | Onde | Origem |
|---|---|---|
| Emblema a1 (escudo da Rúbia com chave inglesa e martelo) | Cabeçalho, ao lado do nome em texto; `apple-touch-icon.png` e os ícones de 192 e 512 px do manifest | fonte externa, modelo `fonte externa` (fonte externa), **só por texto**, tarefa `6a4e850a` |
| Selo b1 ("GOBLINSHIFT · OFICINA"), marca secundária | Página de créditos e imagem de compartilhamento (`og-image.jpg`) | Mesmo modelo, só por texto, tarefa `81de143f` |
| Ícone da aba (`favicon.svg` e `favicon.ico`) | Aba do navegador | **Obra própria**: a cabeça da Rúbia desenhada à mão em SVG, na grade de 32 px, com processamento determinístico |

Edição humana do a1 e do b1 (`scripts/arte/gerar-marca.sh`): limpeza do alfa (o platô opaco vinha com 249 a 254 e os pixels transparentes carregavam cor escura, que vazava como halo ao reduzir), recorte justo, conversão AVIF e WebP por altura, os ícones quadrados sobre madeira e a composição da imagem de compartilhamento com o título na Bricolage Grotesque (`scripts/arte/render-marca.mjs`). A ilustração continua raster: não foi vetorizada.

**A marca não é licenciada** (decisão J-13; Termos de Uso, item 9): o nome GoblinShift, o emblema, o selo, o ícone da aba e os nomes dos personagens ficam reservados, inclusive a parte humana do trabalho sobre eles. A CC BY 4.0 da seção "Direito autoral e licença" não os alcança.

## Jade e Fagulha: publicadas por decisão do usuário

A Jade e a Fagulha seguem outra cadeia: um modelo gerado pelo usuário direto no site do fonte externa, um render nosso dele, uma versão semi-realista no fonte externa (pela fonte externa) e o modelo final no fonte externa. A origem do primeiro passo não é nossa:

- **Jade** (fonte externa `3c7aa397`): deriva do modelo `cab576c8`, que é idêntico, byte a byte, ao 3D gerado a partir de `referencias-usuario/ref2-jeans.png`, uma imagem de terceiros trazida como referência.
- **Fagulha** (fonte externa `0d84776f`): deriva do modelo `f37f6132`, gerado direto no site do fonte externa. Não há registro da imagem de entrada, então a origem não pode ser verificada.

**Decisão do usuário, em 2026-10-15:** ele assume o risco de publicar a Jade e libera a Fagulha. A decisão está em `exceptions` no `PROVENANCE.json` (quem, quando e por quê), e o `check-provenance` recusa qualquer outra geração de origem de terceiros que não tenha decisão registrada. Se o autor da imagem de referência pedir, a Jade sai do site: basta tirá-la do `elenco.tsv` e da rotação do estado vazio. O pedido de retirada chega por abuso@goblinshift.dev (Termos de Uso, item 9).

**Jade e Fagulha ficam fora da licença CC BY 4.0 abaixo**, em todos os arquivos que derivam desses modelos: não licenciamos o que talvez não seja nosso.

## Termos dos fornecedores (lidos em 2026-10-16)

O que sustenta a publicação é a **assinatura paga da fonte externa** (e a do fonte externa), não o saldo da carteira de API. Cada geração 2D cita no `PROVENANCE.json` o direito do seu upstream, e o `check-provenance` recusa direito que não bate com o `upstream` declarado.

| Direito (`rights`) | Gerações | Termos |
|---|---|---|
| `fonte externa-paid-fonte externa-fonte externa` | Marca: emblema a1 e selo b1 (fonte externa, pela fonte externa) | fonte externa + fonte externa Terms of Use e Services Agreement 4.1 (vigentes desde 2026-01-01) |
| `fonte externa-paid-google-banana-pro` | Versões semi-realistas (fonte externa, pela fonte externa) | fonte externa + fonte externa API (2026-04-28): "Google won't claim ownership over that content" |
| `fonte externa-paid-bytedance-fonte externa` | Conceitos e versões frontais (fonte externa, pela fonte externa) | fonte externa + termos da ByteDance/BytePlus: **cláusula de titularidade da saída não localizada** |
| `fonte externa-plano-pago` | Modelos 3D (fonte externa fonte externa) | Termos e preços do fonte externa |

- **fonte externa** (termos, atualizados em 2025-07-11): 5.2.2 dá ao "Paid User" todos os direitos, inclusive licenciar; 5.2.1 deixa os direitos com a fonte externa no plano Free; 3.4 manda a "Third Party Output" seguir os termos do terceiro.
- **Cessão do upstream vai à fonte externa.** Nos termos da fonte externa, a saída é cedida ao cliente da fonte externa, que aqui é a fonte externa; para Google e ByteDance, o que a fonte externa recebe não foi verificado. O repasse ao usuário depende da fonte externa 5.2.2/3.4 e **não foi verificado**.
- **fonte externa:** "any use" só nos planos Creator ou superiores, e os termos não cedem a propriedade da saída. O usuário é assinante (informado em 2026-10-15), mas o plano exato não foi conferido e **não se sabe se o plano vale para o que foi gerado antes da assinatura ou do upgrade**.

Riscos que ficam para advogado (também em [`docs/legal/notas-internas.md`](../legal/notas-internas.md)):

- cadeia de cessão do logo (fonte externa → fonte externa → usuário), que não foi verificada;
- saída de processamento externo possivelmente não protegível e não exclusiva, o que afeta o registro da marca;
- "Paid User" não é definido nos termos da fonte externa;
- retroatividade do plano do fonte externa;
- indenizações e limites de responsabilidade dos termos dos fornecedores;
- jurisdição estrangeira nos termos dos fornecedores.

## Direito autoral e licença

O US Copyright Office (relatório *estudo de direitos autorais registrado externamente*, 29 de janeiro de 2025) conclui que prompt sozinho não faz de ninguém autor. A saída bruta do fonte externa, da fonte externa e dos modelos de imagem pode não ser protegível, e este repositório não afirma autoria exclusiva sobre ela.

A **contribuição humana** (seleção, renders, enquadramento, recorte e otimização dos modelos) fica sob [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), **exceto Jade e Fagulha** (seção anterior) **e a marca** (seção "A marca"), sem garantia de que a saída de produção editorial seja protegível por direito autoral. Crédito sugerido: "Arte do GoblinShift (https://goblinshift.dev), CC BY 4.0".

O nome GoblinShift, o logotipo (o emblema, o selo e o ícone da aba, seção "A marca") e os nomes dos personagens não são licenciados e não podem ser usados de modo que sugira vínculo ou endosso.

O código, inclusive os scripts de `scripts/arte/`, segue a [Apache License 2.0](../../LICENSE) do repositório. Essa licença não cobre `web/src/assets/art/`, os ícones e a imagem de compartilhamento de `web/public/`, `docs/art/` nem os modelos 3D (arquivo [`NOTICE`](../../NOTICE)). Os Termos de Uso dizem o mesmo no item 9.
