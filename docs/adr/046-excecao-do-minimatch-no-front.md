# ADR 046. Exceção do `minimatch` vulnerável nas ferramentas do front

- **Estado:** substituído pelo [ADR 052](052-minimatch-fora-das-faixas-vulneraveis.md) (2026-11-12). O texto abaixo fica como registro; o `tests/test_minimatch_exception.py` que ele descreve foi trocado por `tests/test_minimatch_fora_da_faixa.py`.
- **Plano:** [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 042](042-auditoria-de-dependencias-e-codeql-fora-do-gate.md) (CVE que não se aplica vira exceção escrita em ADR), [ADR 044](044-auditoria-e-codeql-obrigatorios-no-merge.md)

## Contexto

O alerta 3 do Dependabot aponta o GHSA-g3hv-75wg-m249 (CVE-2026-118764, severidade média) no `minimatch` do `web/package-lock.json`. A faixa vulnerável vai de 7.0.0 até antes de 11.0.1, e não há correção na linha 10. O defeito é uma negação de serviço: com `optimizationLevel >= 2`, `firstPhasePreProcess()` multiplica as partes de um padrão `**/../` escolhido pelo atacante, e `secondPhasePreProcess()` as compara duas a duas. Quem não controla o padrão nem liga esse nível não é afetado.

Fatos conferidos em 09/11/2026, num clone de `cc1dd2cc`:

- O lock tem quatro cópias do `minimatch` 10.2.6, todas marcadas `dev`: em `eslint`, `@eslint/config-array`, `@typescript-eslint/typescript-estree` e `type-coverage-core`. As cópias 3.1.5 e 5.1.9 estão fora da faixa.
- `npm audit --omit=dev` acha 0 vulnerabilidades. O `npm audit` completo acha 12, todas o mesmo GHSA, propagado pelos pacotes que dependem dessas quatro cópias.
- Nenhum arquivo do `node_modules` passa `optimizationLevel`. As únicas ocorrências são a leitura com padrão 1 dentro do próprio `minimatch` e de cópias embutidas no Playwright e no Prettier, também com padrão 1. O repositório não menciona a opção.
- Os padrões vêm só de arquivos versionados: `eslint.config.js`, `tsconfig*.json`, `knip.json` e os scripts do `package.json`. Nenhuma ferramenta recebe glob de usuário, de rede ou de upload.
- A imagem do front (`docker/web.Dockerfile`) roda `npm ci` e `vite build` no estágio `build` e copia só o `dist/` para o Nginx. O `minimatch` não chega à produção.
- Nenhum pai aceita `^11`: as últimas versões publicadas pedem `^10.2.6` (`eslint` 10.14.0), `^10.2.4` (`@eslint/config-array` 0.23.5), `^10.2.2` (`@typescript-eslint/typescript-estree` 8.72.2) e `6 || 7 || 8 || 9 || 10` (`type-coverage-core` 2.31.2).

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Exceção por ADR, com teste que trava a exceção** (escolhida) | Nenhuma versão fora do que os pais declaram. A exceção fica escrita e um teste reprova qualquer mudança que a invalide | O alerta é dispensado à mão no GitHub, e o `npm audit` completo continua acusando 12 achados até um pai aceitar `^11` |
| **B. `overrides` forçando `minimatch` 11.0.5** | Zera o `npm audit` e fecha o alerta sem dispensa | Força uma major que nenhum pai declara aceitar. Passar no lint, no `tsc`, no `vitest` e no build hoje não garante o próximo `eslint` ou `typescript-eslint`, que testam só com a `^10`. Cada atualização desses pais vira uma verificação manual de compatibilidade |
| **C. Esperar, sem registro** | Nenhum trabalho agora | O alerta fica aberto sem decisão, contra o ADR 042, e um caminho novo na faixa passaria sem ninguém notar |
| **D. Trocar as ferramentas** | Sai a dependência | Desproporcional: o defeito não alcança este uso |

## Decisão

Alternativa A. As quatro cópias do `minimatch` 10.2.6 ficam aceitas enquanto valerem as três condições: só desenvolvimento, nenhum `optimizationLevel >= 2` e padrões só do repositório. O alerta 3 do Dependabot é dispensado como *vulnerable code is not actually used*, com link para este ADR. A dispensa é do dono, ou de quem ele autorizar, e não deste repositório.

A alternativa B foi testada: com `overrides` para 11.0.5 nos quatro pais, `npm audit` deu 0, e `npm run lint`, `npm run typecheck`, `vitest` e `npm run build` passaram. Ela não foi adotada pelo motivo da tabela, não por quebrar.

`tests/test_minimatch_exception.py` lê o `web/package-lock.json` e exige que as cópias na faixa vulnerável sejam exatamente os quatro caminhos aceitos, em 10.2.6 e marcadas `dev`.

## Gatilhos de revisão

Revisar este ADR, e retirar a exceção quando possível, em qualquer um destes casos:

- Um dos quatro pais publicar versão que aceite `minimatch` `^11`. O Dependabot propõe a atualização, o lock muda e o teste reprova até o ADR e o teste serem ajustados.
- Uma cópia nova do `minimatch` entrar na faixa, uma cópia mudar de versão ou perder a marca `dev`. O teste reprova.
- O GHSA-g3hv-75wg-m249 mudar de faixa ou de condição, ou surgir outro advisory no `minimatch`. O Dependabot abre um alerta novo, e a dispensa do alerta 3 não o cobre.
- Alguma ferramenta passar a receber glob de fora do repositório, ou alguém configurar `optimizationLevel`.
- Em 09/02/2027, três meses após a decisão, se nada acima tiver acontecido antes.

## Consequências

- O `npm audit` completo segue acusando 12 achados moderados. Quem rodar deve ler este ADR antes de usar `npm audit fix --force`.
- Uma atualização de `eslint`, `typescript-eslint` ou `type-coverage` que mude essas cópias reprova o teste. O PR do Dependabot precisa então atualizar o teste e este ADR, ou retirar a exceção.
- As cópias embutidas do `minimatch` no Playwright e no Prettier não aparecem no lock nem no Dependabot. Elas também só servem a desenvolvimento e usam o nível padrão 1, mas o teste não as vê.

## Compliance

- `tests/test_minimatch_exception.py` confere os caminhos, a versão e a marca `dev` das cópias na faixa vulnerável.
- `npm audit --omit=dev` em `web/` deve dar 0.
- O estado do alerta é conferido com `gh api repos/{owner}/{repo}/dependabot/alerts/3` depois da dispensa.
