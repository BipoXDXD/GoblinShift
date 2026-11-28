# ADR 066. Versão 2 de receita ao lado da 1, sobre um plano interno com dois adapters

- **Estado:** Aceito (2026-11-22, por decisão do dono: D1 = a, D2 = a, D3 = a, D5 = a, plano
  `paridade-image-forge-e-media-mill-v2`)
- **Plano:** [1.6](../GoblinShiftPlan.md#s1-6) (versões e ciclo de vida), 1.2 (Image Forge), 3.2
  (registro como `dict` simples)
- **Relacionado:** #564; [ADR 018](018-pillow-na-imagem-do-executor.md),
  [ADR 021](021-digest-da-imagem-em-context-json.md),
  [ADR 061](061-contrato-v1-so-aditivo-no-ci.md); pesquisa
  `docs/pesquisa/concorrencia-imagem-e-midia.md`, §2

## Contexto

A pesquisa de concorrência apontou lacunas no Image Forge (entrada WebP e GIF, saída WebP, `cover`
com corte, `pad`, escala em %, ajustes prontos) e no Media Mill (áudio com perda, corte). Nenhuma
cabe na v1: dentro de uma versão publicada, schema, defaults, enums e semântica não mudam (1.6).

O mecanismo de versão já existia: o catálogo (`catalog/versions.py`) e o registro
(`processing/registry.py`) são chaveados por `(recipe_id, version)`, mas toda receita só tinha a
v1. Faltava decidir como a v2 compartilha código com a v1 sem mudá-la.

## Decisão

- **Plano interno por receita, com dois adapters.** O Image Forge roda um `ForgePlan`
  (`image_forge/plan.py`), e o Media Mill vai rodar um `MillPlan`. São dataclasses congeladas, sem
  PIL nem FFmpeg, com tudo o que o pipeline precisa: formatos aceitos, geometria, codificação e
  corte. `plan_from_v1(Params)` e `plan_from_v2(ParamsV2)` montam o plano; `ImageForgeV1` e
  `ImageForgeV2` só chamam `forge(plan, ...)`. As suítes da v1 não mudaram e provam que o
  comportamento dela é o mesmo, byte a byte no `contain`.
- **Nenhum padrão de classe.** A variação entre versões é dado (formatos, modo de ajuste, encoder),
  não algoritmo trocável em execução. A escolha vira `StrEnum` + `match` exaustivo (`FitMode`,
  `OutputFormatV2`), e o registro continua um `dict`.
- **Enums próprios da v2.** `OutputFormatV2` não estende o `OutputFormat` da v1: o portão do
  contrato (ADR 061) aceita valor novo em enum de entrada, então nada impediria alguém de
  acrescentar `webp` à v1. O snapshot de `tests/catalog/congeladas/` (schema e defaults de cada
  versão publicada) é a prova de que a v1 não muda.
- **A v2 é superconjunto da v1.** Mesmos campos, faixas e defaults, mais os novos com defaults que
  reproduzem a v1. Um teste de propriedade confere que parâmetros válidos da v1, resolvidos pela
  v2, dão o mesmo plano, só com os formatos de entrada a mais. É isso que permite à Bancada abrir
  uma execução ou um preset da v1 no formulário da v2.
- **Formatos de entrada não são parâmetro.** A v1 continua recusando WebP e GIF pela lista de
  formatos do plano (`formats.py`), e não por uma opção do usuário. Por isso converter `Params` da
  v1 em `ParamsV2` não serviria.
- **Os `details` da v1 ficam como estão.** Só a v2 registra o modo de ajuste (`fit`) e, conforme
  ele, a escala, o recorte ou o tamanho do conteúdo (`ForgePlan.fit_details`).
- **Ciclo de vida da v1 (D1 = a).** A v1 passa a `deprecated` no PR da Bancada de cada receita, com
  `sunset_at` no primeiro dia do mês depois de 90 dias do merge, e sai num PR posterior à data. Até
  lá, um preset da v1 executado pela API roda a v1, com `Sunset`. Enquanto a Bancada não ganha o
  formulário da v2, as duas versões ficam `current` e a Bancada escreve a v1
  (`web/src/recipes/types.ts`, `FormVersions`).
- **Ajustes prontos no catálogo (D2 = a).** Ficam em `RecipeVersion.system_presets`, validados pelo
  modelo da versão no import e publicados em `GET /catalog`. Não são linhas de `preset`: têm `key`
  em vez de UUID e nunca aparecem em `/presets`, então não colidem com os presets da conta.
- **Imagem do executor.** A imagem só muda pelo código copiado de `processing`, e o digest novo vem
  no build da release (ADR 021). Runs aceitas antes do deploy continuam na imagem fixada nelas.

## Alternativas descartadas

- **Template Method** (`ImageForgeBase` com ganchos): herança para variar dados, e cada versão nova
  acrescentaria ganchos à base, mudando a v1 por tabela.
- **Strategy em classes** (uma classe por modo de ajuste ou formato): mais tipos e indireção para o
  que um `match` sobre um enum resolve, sem troca em tempo de execução que a justifique.
- **Copiar o pipeline da v1:** cerca de 180 linhas em dobro, e um conserto de segurança teria de
  ser feito duas vezes.
- **Estender os enums da v1:** muda uma versão publicada, que o plano proíbe.
- **Ajustes prontos só no front, ou em linhas de uma conta "sistema":** o primeiro deixa a API sem
  conhecê-los e os valida só no zod; o segundo põe dado de produto no banco, com colisão de nome,
  migration e regra de acesso novas.

## Consequências

- Uma versão nova de receita é um modelo no catálogo, um snapshot congelado, um adapter de
  parâmetros para o plano e uma entrada no registro. O pipeline continua um só.
- Duas versões `current` da mesma receita convivem até o PR da Bancada, contra a letra de "a atual
  e a anterior" de 1.6; o PR da Bancada fecha isso com a `deprecated` da v1.
- O front deriva os tipos por `recipe_id`: com duas versões no contrato, o formulário de cada
  receita declara a versão que escreve.
