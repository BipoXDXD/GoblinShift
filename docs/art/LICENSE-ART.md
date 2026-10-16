# Arte do site: origem, direitos e licença

As goblins do site são **ilustrações e modelos 3D gerados com processamento externo e editados**. Este arquivo registra de onde vieram, com base em quais termos estão no site e sob qual licença está a parte humana do trabalho. O registro arquivo a arquivo, com sha256 e a cadeia de gerações, fica em [`web/src/assets/art/PROVENANCE.json`](../../web/src/assets/art/PROVENANCE.json), conferido no CI por `web/scripts/check-provenance.ts` ([ADR 030](../adr/030-motor-e-asset-do-3d-da-abertura.md)).

## O que está no site

| Goblin   | Onde                                                                                                | Modelo 3D (fonte externa fonte externa)                                                                                                                                             |
| -------- | --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Rúbia    | Cena 3D da abertura e a imagem estática dela; estado carregando, em rotação com a Fagulha; créditos | `9f030f3c` (regeração de 1 milhão de triângulos da `e1c79f67`, mesma imagem de referência) |
| Lua      | Estado vazio, em rotação com a Jade; créditos                                                       | `81ae6621`                                                                                 |
| Nyx      | Estado de erro; créditos                                                                            | `19a1ddba`                                                                                 |
| Ferrugem | Estado de sucesso; créditos                                                                         | `ec3a728c`                                                                                 |
| Jade     | Estado vazio, em rotação com a Lua; créditos                                                        | `3c7aa397`                                                                                 |
| Fagulha  | Estado carregando, em rotação com a Rúbia; créditos                                                 | `0d84776f`                                                                                 |

Cadeia da Rúbia, da Lua, da Nyx e da Ferrugem (detalhe no `PROVENANCE.json`):

1. Conceito 2D **só por texto** no fonte externa (ByteDance), pela API da fonte externa.
2. Versão frontal e versão semi-realista por image-to-image (fonte externa e fonte externa, do Google, pela fonte externa), tendo como entradas **só imagens nossas**: o conceito do passo 1 e renders nossos de modelos do fonte externa.
3. Modelo 3D no fonte externa fonte externa Gen-2.5 a partir da imagem do passo 2.
4. Edição humana: render no Blender, recorte e conversão (`scripts/arte/gerar-imagens.sh`); simplificação da malha, redimensionamento de textura e compressão do GLB (`scripts/arte/otimizar-glb.sh`).

A Ada (fonte externa `417f7fe0`) saiu do site em 2026-10-15, por decisão do usuário; ela fica em `excluded` no `PROVENANCE.json`.

## Jade e Fagulha: publicadas por decisão do usuário

A Jade e a Fagulha seguem outra cadeia: um modelo gerado pelo usuário direto no site do fonte externa, um render nosso dele, uma versão semi-realista no fonte externa (pela fonte externa) e o modelo final no fonte externa. A origem do primeiro passo não é nossa:

- **Jade** (fonte externa `3c7aa397`): deriva do modelo `cab576c8`, que é idêntico, byte a byte, ao 3D gerado a partir de `referencias-usuario/ref2-jeans.png`, uma imagem de terceiros trazida como referência.
- **Fagulha** (fonte externa `0d84776f`): deriva do modelo `f37f6132`, gerado direto no site do fonte externa. Não há registro da imagem de entrada, então a origem não pode ser verificada.

**Decisão do usuário, em 2026-10-15:** ele assume o risco de publicar a Jade e libera a Fagulha. A decisão está em `exceptions` no `PROVENANCE.json` (quem, quando e por quê), e o `check-provenance` recusa qualquer outra geração de origem de terceiros que não tenha decisão registrada. Se o autor da imagem de referência pedir, a Jade sai do site: basta tirá-la do `elenco.tsv` e da rotação do estado vazio. O pedido de retirada chega por abuso@goblinshift.dev (Termos de Uso, item 9).

**Jade e Fagulha ficam fora da licença CC BY 4.0 abaixo**, em todos os arquivos que derivam desses modelos: não licenciamos o que talvez não seja nosso.

## Termos que sustentam a publicação (lidos em 2026-10-15)

- **fonte externa fonte externa** (termos, "Current version", sem data; preços):
  - seção 2: a exportação vale "for private or commercial use depending on your subscription plan";
  - seção 5(b): "we will not limit your use of such Output", e a empresa não garante que a saída seja protegível por direito autoral;
  - página de preços: o plano Creator e os acima dão "Unlimited export and any use".
  - **Base:** o usuário é assinante do fonte externa (informado em 2026-10-15), então as gerações caem no plano pago. Um print do plano ativo na conta reforçaria o registro, mas não é condição.
- **fonte externa** (termos, "Last updated: July 11, 2025"), que gerou as imagens 2D de entrada (elas não estão no site):
  - 5.2.2: "Paid Users generally have all rights" sobre entradas e saídas, e a empresa não treina com elas;
  - 3.2: uso comercial permitido;
  - 3.4: saída de serviço de terceiro (fonte externa e fonte externa) segue os termos do terceiro;
  - **Base:** o usuário é assinante da fonte externa e tem saldo pago na API (informado em 2026-10-15), então é "Paid User" pelos dois caminhos: a assinatura e os créditos de API comprados. Os termos não definem "Paid User", mas uma conta com assinatura ativa se enquadra em qualquer leitura.
- **Google, fonte externa API** (termos, atualizados em 2026-04-28), por trás do fonte externa: "Google won't claim ownership over that content".

## Direito autoral e licença

O US Copyright Office (relatório _estudo de direitos autorais registrado externamente_, 29 de janeiro de 2025) conclui que prompt sozinho não faz de ninguém autor. A saída bruta do fonte externa, da fonte externa e dos modelos de imagem pode não ser protegível, e este repositório não afirma autoria exclusiva sobre ela.

A **contribuição humana** (seleção, renders, enquadramento, recorte e otimização dos modelos) fica sob [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), **exceto Jade e Fagulha** (seção anterior), sem garantia de que a saída de produção editorial seja protegível por direito autoral. Crédito sugerido: "Arte do GoblinShift (https://goblinshift.dev), CC BY 4.0".

O nome GoblinShift, o logotipo e os nomes dos personagens não são licenciados e não podem ser usados de modo que sugira vínculo ou endosso.

O código, inclusive os scripts de `scripts/arte/`, segue a [Apache License 2.0](../../LICENSE) do repositório. Essa licença não cobre `web/src/assets/art/`, `docs/art/` nem os modelos 3D (arquivo [`NOTICE`](../../NOTICE)). Os Termos de Uso dizem o mesmo no item 9.
