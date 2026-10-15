# Arte do site: origem, direitos e licença

As goblins do site são **ilustrações e modelos 3D gerados com IA e editados**. Este arquivo registra de onde vieram, com base em quais termos estão no site e sob qual licença está a parte humana do trabalho. O registro arquivo a arquivo, com sha256 e a cadeia de gerações, fica em [`web/src/assets/art/PROVENANCE.json`](../../web/src/assets/art/PROVENANCE.json), conferido no CI por `web/scripts/check-provenance.ts` ([ADR 030](../adr/030-motor-e-asset-do-3d-da-abertura.md)).

## O que está no site

| Goblin | Onde | Modelo 3D (Hyper3D Rodin) |
|---|---|---|
| Rúbia | Cena 3D da abertura e a imagem estática dela | [`e1c79f67`](https://hyper3d.ai/workspace/rodin/e1c79f67-dafb-4f23-89ea-f4324beca492) |
| Lua | Estado vazio | [`81ae6621`](https://hyper3d.ai/workspace/rodin/81ae6621-dbb6-48b0-acb0-04918bfdb393) |
| Ada | Estado carregando | [`417f7fe0`](https://hyper3d.ai/workspace/rodin/417f7fe0-23a6-4fec-922a-33fbb3c69032) |
| Nyx | Estado de erro | [`19a1ddba`](https://hyper3d.ai/workspace/rodin/19a1ddba-fd13-461e-9136-8f8bea40cfc9) |
| Ferrugem | Estado de sucesso | [`ec3a728c`](https://hyper3d.ai/workspace/rodin/ec3a728c-d58f-40f4-a298-a144e6ba668b) |

Cadeia de cada uma (detalhe no `PROVENANCE.json`):

1. Conceito 2D **só por texto** no Seedream (ByteDance), pela API da Tripo.
2. Versão frontal e versão semi-realista por image-to-image (Seedream e Banana Pro, do Google, pela Tripo), tendo como entradas **só imagens nossas**: o conceito do passo 1 e renders nossos de modelos do Rodin.
3. Modelo 3D no Hyper3D Rodin Gen-2.5 a partir da imagem do passo 2.
4. Edição humana: render no Blender, recorte e conversão (`scripts/arte/gerar-imagens.sh`); simplificação da malha, redimensionamento de textura e compressão do GLB (`scripts/arte/otimizar-glb.sh`).

## Jade e Fagulha: origem não comprovada

Duas das sete goblins aprovadas têm origem que não podemos comprovar:

- **Jade** (Rodin `3c7aa397`): deriva do modelo `cab576c8`, que é idêntico, byte a byte, ao 3D gerado a partir de `referencias-usuario/ref2-jeans.png`, uma imagem de terceiros trazida como referência.
- **Fagulha** (Rodin `0d84776f`): deriva do modelo `f37f6132`, gerado direto no site do Rodin. Não há registro da imagem de entrada, então a origem não pode ser verificada.

Se aparecem no site, é por decisão do dono do serviço, que assumiu o risco em 2026-10-15; o registro dessa decisão fica no `PROVENANCE.json`. **Jade e Fagulha ficam fora da licença CC BY 4.0 abaixo**, em todos os arquivos que derivam desses modelos: não licenciamos o que talvez não seja nosso. Quem tiver direito sobre a imagem de origem e quiser a retirada pode escrever para abuso@goblinshift.dev (Termos de Uso, item 9).

## Termos que sustentam a publicação (lidos em 2026-10-15)

- **Hyper3D Rodin** ([termos](https://hyper3d.ai/legal/terms), "Current version", sem data; [preços](https://hyper3d.ai/pricing)):
  - seção 2: a exportação vale "for private or commercial use depending on your subscription plan";
  - seção 5(b): "we will not limit your use of such Output", e a empresa não garante que a saída seja protegível por direito autoral;
  - página de preços: o plano Creator e os acima dão "Unlimited export and any use".
  - **Base:** o usuário é assinante do Rodin (informado em 2026-10-15), então as gerações caem no plano pago. Um print do plano ativo na conta reforçaria o registro, mas não é condição.
- **Tripo** ([termos](https://developers.tripo3d.com/en/terms), "Last updated: July 11, 2025"), que gerou as imagens 2D de entrada (elas não estão no site):
  - 5.2.2: "Paid Users generally have all rights" sobre entradas e saídas, e a empresa não treina com elas;
  - 3.2: uso comercial permitido;
  - 3.4: saída de serviço de terceiro (Seedream e Banana Pro) segue os termos do terceiro;
  - **Base:** o usuário é assinante da Tripo e tem saldo pago na API (informado em 2026-10-15), então é "Paid User" pelos dois caminhos: a assinatura e os créditos de API comprados. Os termos não definem "Paid User", mas uma conta com assinatura ativa se enquadra em qualquer leitura.
- **Google, Gemini API** ([termos](https://ai.google.dev/gemini-api/terms), atualizados em 2026-04-28), por trás do Banana Pro: "Google won't claim ownership over that content".

## Direito autoral e licença

O US Copyright Office (relatório *Copyright and Artificial Intelligence, Part 2*, 29 de janeiro de 2025) conclui que prompt sozinho não faz de ninguém autor. A saída bruta do Rodin, da Tripo e dos modelos de imagem pode não ser protegível, e este repositório não afirma autoria exclusiva sobre ela.

A **contribuição humana** (seleção, renders, enquadramento, recorte e otimização dos modelos) fica sob [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), **exceto Jade e Fagulha** (seção anterior), sem garantia de que a saída gerada por IA seja protegível por direito autoral. Crédito sugerido: "Arte do GoblinShift (https://goblinshift.dev), CC BY 4.0".

O nome GoblinShift, o logotipo e os nomes dos personagens não são licenciados e não podem ser usados de modo que sugira vínculo ou endosso.

O código, inclusive os scripts de `scripts/arte/`, segue a [Apache License 2.0](../../LICENSE) do repositório. Essa licença não cobre `web/src/assets/art/`, `docs/art/` nem os modelos 3D (arquivo [`NOTICE`](../../NOTICE)). Os Termos de Uso dizem o mesmo no item 9.
