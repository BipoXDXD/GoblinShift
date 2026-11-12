# ADR 052. `minimatch` fora das faixas vulneráveis por `overrides` com seletor de faixa

- **Estado:** aceito (2026-11-12). Decidido pela coordenação: só afeta ferramentas de desenvolvimento, é reversível com uma linha e não muda nada em produção. Reverte o ADR 046.
- **Plano:** [3.6](../GoblinShiftPlan.md#s3-6)
- **Substitui:** [ADR 046](046-excecao-do-minimatch-no-front.md) (o 046 fica *substituído por 052*)
- **Relacionado:** [ADR 042](042-auditoria-de-dependencias-e-codeql-fora-do-gate.md) (CVE que não se aplica vira exceção escrita em ADR), [ADR 044](044-auditoria-e-codeql-obrigatorios-no-merge.md)

## Contexto

O alerta 3 do Dependabot (GHSA-g3hv-75wg-m249, CVE-2026-118764, severidade média) segue **aberto**: o ADR 046 previa dispensá-lo à mão, e isso nunca foi feito. O defeito é uma negação de serviço em `firstPhasePreProcess()` com `optimizationLevel >= 2` e padrão `**/../` escolhido pelo atacante. A faixa vulnerável é `>= 7.0.0, < 11.0.1`, e não há correção na linha 10.

O PR #329 tentou fechar o alerta com `overrides` em `minimatch` 11.0.1. O check `Verificações` reprovou em `tests/test_minimatch_exception.py`, que trava a exceção do 046, e o PR ficou em draft até haver decisão. Este ADR é essa decisão, e o #329 foi refeito sobre ela.

Fatos conferidos em 11/11/2026, num clone de `40f9cf16` (`main`) e de `3619988c` (#329):

- **Alcance.** O lock tem quatro cópias do 10.2.6, todas `dev`: em `eslint`, `@eslint/config-array`, `@typescript-eslint/typescript-estree` e `type-coverage-core`. As cópias 3.1.5 (raiz) e 5.1.9 (`@redocly/openapi-core`) estão fora da faixa. `npm ls minimatch --omit=dev` sai vazio e `npm audit --omit=dev` dá 0. `uv.lock` e `pyproject.toml` não citam `minimatch`.
- **Produção.** `docker/web.Dockerfile` roda `npm ci` e `vite build` no estágio `build` e copia só `/web/dist/` para o Nginx. Depois de `npm ci` e `npm run build` na `main`, `grep -rIl -i minimatch dist` e `grep -rIlE "optimizationLevel|globparts" dist` dão 0 arquivos.
- **Entrada.** Os globs vêm de `eslint.config.js`, `tsconfig*.json`, `knip.json` e do `package.json`, todos versionados. Nenhum arquivo do repositório cita `optimizationLevel`. Fora das cópias do `minimatch`, só o Playwright e o Prettier têm a opção em código embutido, com o padrão `1`. A CI roda em `pull_request` (não em `pull_request_target`), então um PR de fork roda o `eslint` com a configuração do próprio PR, sem segredo e com `contents: read`. Quem altera o `eslint.config.js` ou o `package.json` do PR já executa código arbitrário no runner, de modo que o defeito não amplia nada (o repositório é público e a aprovação de fork é `first_time_contributors`).
- **Pais.** Nenhum dos quatro declara `^11`, e nenhuma versão publicada o faz: `eslint` 10.14.0 pede `^10.2.6`, `@eslint/config-array` 0.23.5 pede `^10.2.4`, `@typescript-eslint/typescript-estree` 8.72.2 pede `^10.2.2` e `type-coverage-core` 2.31.2 pede `6 || 7 || 8 || 9 || 10`. Não houve 10.x nova: o 10.2.6 é a última. A última do `minimatch` é a 11.0.8. A compatibilidade com a 11 não vem de declaração dos pais, e sim de verificação: lint, `tsc`, `type-coverage` (100%, 55653 de 55653), `knip`, `vitest` (119 arquivos, 2473 testes) e `build` passam com o override aplicado.
- **Advisory novo na 11.** O GHSA-cf2h-pxjq-68xw (publicado em 10/11/2026, severidade média) é uma regressão de `mergeRows` na 11.0.0: tempo de compilação quadrático para padrões longos, em `>= 11.0.0, <= 11.0.6`, corrigido na 11.0.7. Fixar `11.0.1`, como o #329 faz, troca um alerta por outro. O `npm audit` ainda não o mostra (conferido hoje).
- **O que muda na 11.** O casamento deixa de usar regex e passa a usar `@isaacs/nonrecursive-glob-match`; as exportações `makeRe` e `AST` saem do pacote; o `engines` da 11.0.8 é `20 || >=22` (a 11.0.1 aceitava 18). O projeto usa Node 24. Os quatro pais usam só `new Minimatch(...)`, `.match()`, `.pattern` e `minimatch(...)`, que continuam na 11.
- **Teste do #329 e da alternativa.** Com `overrides` por pai em `11.0.1`: `npm ci` ok, `npm audit` 0, `lint`, `typecheck` e `type-coverage` (100%) passam; as duas funções de `test_minimatch_exception.py` falham, como esperado. Com uma única entrada `"minimatch@>=7 <11": "^11.0.7"` sobre o lote de integração #331 (base deste PR): as quatro cópias resolvem para 11.0.8 (mais o pacote `@isaacs/nonrecursive-glob-match` 1.1.0, só `dev`), 3.1.5 e 5.1.9 ficam como estão, `npm audit` e `npm audit --omit=dev` dão 0, e `lint`, `typecheck`, `type-coverage` (100%), `knip`, `vitest` (119 arquivos, 2473 testes) e `build` passam.
- **Sem `npm audit` na CI.** Nenhum workflow roda `npm audit`; os 12 achados do `npm audit` completo só incomodam quem roda o comando e mantêm o alerta 3 aberto.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Manter o 046** | Nada muda no lock. A recusa do 046 segue verdadeira: nenhum pai aceita `^11`. Alcance nulo na produção | O alerta 3 segue aberto até o dono dispensá-lo à mão. O `npm audit` completo acusa 12 achados. Exige revisão agendada (09/02/2027) e o teste que trava quatro caminhos |
| **B. Override do #329 como está (11.0.1 por pai)** | Zera o alerta 3 | Fixa uma versão dentro do GHSA-cf2h (`>= 11.0.0, <= 11.0.6`): abre um alerta novo. Uma entrada por pai, que não some quando o pai aceitar `^11` |
| **C. Uma entrada com seletor de faixa, `"minimatch@>=7 <11": "^11.0.7"`** (recomendada) | Fica acima dos dois advisories. Atinge só as arestas que pedem a faixa vulnerável: 3.1.5 e 5.1.9 não são tocadas, e um pai que passar a pedir `^11` sai do seletor sozinho. Zera o `npm audit` e fecha o alerta sem dispensa. Uma linha para manter | Força uma major que nenhum pai declara aceitar. Cada PR do Dependabot dos pais é a verificação de compatibilidade, e a CI o faz (lint, `tsc`, type-coverage, knip, vitest, build, todos sobre as APIs usadas). O comportamento de expirar sozinho vem da semântica de seletor do npm e não foi testado com um pai real em `^11` |
| **D. Override por pai com `^11.0.7`** | Mesmo efeito de C, com escopo explícito por pai | Quatro entradas, que ficam mortas à medida que os pais atualizam e que o Dependabot não remove |
| **E. Atualizar um pai** | Nenhum override | Impossível hoje: nenhuma versão publicada dos quatro pais aceita `^11` |
| **F. Trocar as ferramentas ou esperar sem registro** | Sai a dependência, ou nenhum trabalho | Desproporcional, ou contra o ADR 042 (alerta aberto sem decisão) |

## Decisão

Alternativa C. O `web/package.json` ganha em `overrides` uma entrada `"minimatch@>=7 <11": "^11.0.7"`, e o `web/package-lock.json` é regenerado. O piso `11.0.7` existe por causa do GHSA-cf2h-pxjq-68xw: nenhum valor menor entra. O #329 usa essa entrada (em vez das quatro de 11.0.1).

O ADR 046 passa a **substituído por 052**: o estado dele muda, o texto fica. A recusa que ele fez da alternativa B valia enquanto se esperava que a CI não pegasse incompatibilidade; ela pega, e a segunda correção na linha 11 (11.0.7) mostra que fixar uma versão exata seria pior que um piso. Nenhum risco de segurança muda na produção, nas duas direções.

`tests/test_minimatch_exception.py` é trocado por `tests/test_minimatch_fora_da_faixa.py`, que lê o `web/package-lock.json` e exige que nenhuma cópia caia em `>= 7.0.0, < 11.0.1` nem em `>= 11.0.0, < 11.0.7`. Um terceiro teste prova que o leitor do lock acha cópias do `minimatch`, para o teste não passar em silêncio se o formato do lock mudar. O teste de `dev` sai, porque não há mais exceção a travar.

## Gatilhos de revisão

Revisar este ADR, e tirar o override, quando:

- Os quatro pais passarem a aceitar `minimatch` `^11` (`npm view` dos quatro). O override fica sem efeito e deve ser removido na mesma PR.
- Surgir advisory novo no `minimatch` 11 ou algum pai reprovar na CI com a 11. O Dependabot abre o alerta, e a resposta é subir o piso, não voltar à 10.
- Em 12/02/2027, três meses após a decisão, se nada acima tiver acontecido antes.

## Consequências

- `npm audit` completo passa a dar 0 e o alerta 3 fecha sozinho quando o lock entrar na `main`. Ninguém precisa dispensar alerta no GitHub.
- Um PR do Dependabot para `eslint`, `typescript-eslint` ou `type-coverage` herda o override. Se a 11 quebrar algo, a CI do próprio PR reprova.
- Pelo que se sabe, o Dependabot não atualiza nem remove entradas de `overrides` (comportamento não testado aqui). A remoção é manual, no gatilho acima. O `dependabot.yml` não muda.
- O `minimatch` 11.0.8 exige Node `20 || >=22`. O `web/.nvmrc` e a imagem usam o 24.
- As cópias embutidas no Playwright e no Prettier não aparecem no lock e seguem como estão; usam o nível padrão `1`.

## Reversibilidade e risco

Reversível por completo: tirar uma linha do `package.json` e regenerar o lock devolve as cópias 10.2.6, e o 046 volta a `aceito` com o teste antigo. Não toca dados, produção nem imagem. Não há risco de segurança em produção hoje, antes ou depois: nenhuma cópia entra no `dist/` (conferido), e o defeito exige `optimizationLevel >= 2` com glob do atacante, que nenhuma ferramenta usa.

## Compliance

- `tests/test_minimatch_fora_da_faixa.py` reprova qualquer cópia nas duas faixas vulneráveis e reprova se o leitor do lock não achar nenhuma cópia.
- `npm audit` em `web/` deve dar 0.
- `grep -rIl -i minimatch dist` depois de `npm run build` deve continuar vazio.
- O alerta 3 é conferido com `gh api repos/{owner}/{repo}/dependabot/alerts/3` depois do merge (`state: fixed`).
