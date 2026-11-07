# ADR 044. Auditoria de dependências e CodeQL obrigatórios no merge

- **Estado:** aceito
- **Plano:** [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 042](042-auditoria-de-dependencias-e-codeql-fora-do-gate.md) (workflows próprios, fora do gate da release)

## Contexto

O ADR 042 deixou `pip-audit` e CodeQL fora do gate da release e registrou que um achado só bloquearia o merge se o ruleset da `main` listasse esses checks. Os dois rodam em todo PR, sem filtro de caminho, mas hoje um PR pode entrar com achado aberto.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Obrigatórios no ruleset** (escolhida) | Nenhum PR entra com CVE conhecida no lock ou alerta novo do CodeQL. O gate da release e o `ci.yml` não mudam | Uma CVE nova reprova o `pip-audit` em todo PR até a correção entrar. Merge fica mais lento |
| **B. Manter fora do ruleset** | Merge nunca espera uma CVE recém-publicada | Achado depende de alguém olhar a execução. O aviso vem depois do merge |
| **C. Só o `pip-audit` obrigatório** | Menos checks no merge | Alerta novo do CodeQL segue entrando |

## Decisão

Alternativa A. O ruleset da `main` passa a exigir `Dependências Python (pip-audit)`, `CodeQL (python)`, `CodeQL (javascript-typescript)` e o check `CodeQL` da análise de código, que é o que reprova alerta novo. Os dois jobs de análise só provam que a análise rodou.

O ADR 042 continua valendo para a release: esses checks seguem fora de `REQUIRED`, e uma CVE nova não impede publicar uma versão já mesclada.

## Consequências

- Quando uma CVE nova reprovar o `pip-audit`, o PR de correção é o primeiro da fila: ele atualiza o `uv.lock` e passa no próprio check. Os demais esperam ou atualizam a branch depois.
- CVE sem versão corrigida trava todo merge até haver exceção escrita em ADR, como o ADR 042 pede. Essa exceção é decisão do dono.
- O ruleset é configuração do GitHub, fora deste repositório. Mudar o nome de um desses jobs exige ajustar o ruleset no mesmo momento, ou todo PR fica esperando um check que não vem.

## Compliance

- `tests/test_security_workflows.py` confere nomes, gatilhos e ausência de exceção escondida nos dois workflows.
- O ruleset é conferido com `gh api repos/{owner}/{repo}/rulesets` depois de cada mudança.
