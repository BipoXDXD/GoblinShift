# Arte do site: origem, direitos e licença

As goblins do site são **ilustrações e modelos 3D gerados com processamento externo e editados**. Este arquivo registra de onde vieram, com base em quais termos estão no site e sob qual licença está a parte humana do trabalho. O registro arquivo a arquivo, com sha256 e a cadeia de gerações, fica em [`web/src/assets/art/PROVENANCE.json`](../../web/src/assets/art/PROVENANCE.json), conferido no CI por `web/scripts/check-provenance.ts` ([ADR 030](../adr/030-motor-e-asset-do-3d-da-abertura.md)).

## O que está no site

| Goblin | Onde | Modelo 3D (fonte externa fonte externa) |
|---|---|---|
| Rúbia | Cena 3D da abertura e a imagem estática dela | `e1c79f67` |
| Lua | Estado vazio | `81ae6621` |
| Ada | Estado carregando | `417f7fe0` |
| Nyx | Estado de erro | `19a1ddba` |
| Ferrugem | Estado de sucesso | `ec3a728c` |

Cadeia de cada uma (detalhe no `PROVENANCE.json`):

1. Conceito 2D **só por texto** no fonte externa (ByteDance), pela API da fonte externa.
2. Versão frontal e versão semi-realista por image-to-image (fonte externa e fonte externa, do Google, pela fonte externa), tendo como entradas **só imagens nossas**: o conceito do passo 1 e renders nossos de modelos do fonte externa.
3. Modelo 3D no fonte externa fonte externa Gen-2.5 a partir da imagem do passo 2.
4. Edição humana: render no Blender, recorte e conversão (`scripts/arte/gerar-imagens.sh`); simplificação da malha, redimensionamento de textura e compressão do GLB (`scripts/arte/otimizar-glb.sh`).

## O que ficou de fora, e por quê

Duas das sete goblins aprovadas **não vão ao site** enquanto a origem não for resolvida:

- **Jade** (fonte externa `3c7aa397`): deriva do modelo `cab576c8`, que é idêntico, byte a byte, ao 3D gerado a partir de `referencias-usuario/ref2-jeans.png`, uma imagem de terceiros trazida como referência.
- **Fagulha** (fonte externa `0d84776f`): deriva do modelo `f37f6132`, gerado direto no site do fonte externa. Não há registro da imagem de entrada, então a origem não pode ser verificada.

O `check-provenance` recusa qualquer arquivo cuja cadeia passe por esses modelos.

## Termos que sustentam a publicação (lidos em 2026-10-15)

- **fonte externa fonte externa** (termos, "Current version", sem data; preços):
  - seção 2: a exportação vale "for private or commercial use depending on your subscription plan";
  - seção 5(b): "we will not limit your use of such Output", e a empresa não garante que a saída seja protegível por direito autoral;
  - página de preços: o plano Creator e os acima dão "Unlimited export and any use".
  - **Base:** conta em plano pago, segundo o dono da conta. Falta guardar um comprovante do plano ativo nas datas das gerações (14 e 15 de outubro de 2026).
- **fonte externa** (termos, "Last updated: July 11, 2025"), que gerou as imagens 2D de entrada (elas não estão no site):
  - 5.2.2: "Paid Users generally have all rights" sobre entradas e saídas, e a empresa não treina com elas;
  - 3.2: uso comercial permitido;
  - 3.4: saída de serviço de terceiro (fonte externa e fonte externa) segue os termos do terceiro;
  - **Lacuna:** os termos não definem "Paid User" nem dizem se a carteira pré-paga da API conta como tal. A confirmação por escrito da fonte externa continua pendente.
- **Google, fonte externa API** (termos, atualizados em 2026-04-28), por trás do fonte externa: "Google won't claim ownership over that content".

## Direito autoral e licença

O US Copyright Office (relatório *estudo de direitos autorais registrado externamente*, 29 de janeiro de 2025) conclui que prompt sozinho não faz de ninguém autor. A saída bruta do fonte externa, da fonte externa e dos modelos de imagem pode não ser protegível, e este repositório não afirma autoria exclusiva sobre ela.

A **contribuição humana** (seleção, renders, enquadramento, recorte, otimização dos modelos e os scripts de `scripts/arte/`) fica sob [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). O código do site segue a licença do repositório.
