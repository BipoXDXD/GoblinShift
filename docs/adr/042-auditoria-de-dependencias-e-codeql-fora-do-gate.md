# ADR 042. Auditoria de dependências e CodeQL fora do gate da release

- **Estado:** proposto
- **Plano:** [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 033](033-segredos-de-deploy-no-environment-production.md) (publicação só pela `main`)

## Contexto

A CI tinha gitleaks, zizmor, Trivy, Schemathesis e as regras `S` do ruff, mas nenhum SAST dedicado e nenhuma auditoria das dependências Python do `uv.lock`. O Dependabot só avisa depois, numa aba que nada obriga a olhar.

O gate da release (`deploy/coolify/release/gate.py`) aceita um commit só se os jobs do `ci.yml` forem exatamente os de `REQUIRED`: job a mais, a menos ou repetido recusa a publicação. Uma análise nova tem de decidir de que lado desse contrato fica.

## Alternativas

| Alternativa | Prós | Contras |
|-------------|------|---------|
| **A. Workflows próprios** (escolhida) | O gate e o orçamento do commit stage não mudam. A execução semanal acha CVE nova sem depender de push. Permissão de gravar resultados só onde é usada | Não bloqueia a release: só bloqueia o merge se o dono marcar os checks como obrigatórios no ruleset |
| **B. Jobs no `ci.yml`, em `REQUIRED`** | Nenhum commit publica com achado aberto | Uma CVE publicada hoje, sem mudança aqui, reprova a `main` e trava também a publicação da correção. O CodeQL pesa no tempo da CI. O `ci.yml` não tem gatilho semanal |
| **C. CodeQL em default setup** | Sem arquivo para manter | Sem versão fixada por SHA, sem revisão por PR e sem o filtro da cópia do Swagger UI |

## Decisão

Alternativa A. `dependency-audit.yml` roda o `pip-audit` em versão fixada contra a exportação do `uv.lock`, com todos os grupos e com hashes, em PR, push na `main` e toda semana. `codeql.yml` analisa Python e JavaScript/TypeScript sem build, em PR e toda semana. Nenhum dos dois usa segredo. Só o job do CodeQL pede `security-events: write`.

Não há lista de CVEs ignoradas. Se uma CVE não se aplicar ao projeto, a exceção é decidida por escrito, num ADR, e não fica escondida numa flag.

## Consequências

- Um achado não impede a publicação. Quem responde a ele é o PR de correção, ou o aviso de falha da execução semanal.
- A primeira análise do CodeQL varre o código todo. O PR só anota linhas que ele mesmo muda, mas o resultado completo fica na aba de segurança e precisa de triagem.
- O `uvx` instala o `pip-audit` pela versão, sem hash, como já acontece com o zizmor. Pôr a ferramenta no `uv.lock` traria dezenas de pacotes para o lock que ela mesma audita.
- Para que um achado bloqueie o merge, o ruleset da `main` precisa listar os checks novos. É configuração do GitHub, fora deste repositório. O [ADR 044](044-auditoria-e-codeql-obrigatorios-no-merge.md) fez isso. Desde o ADR 044, o check `CodeQL` da análise de código é exigido pelo ruleset da `main`.

## Compliance

- `tests/test_security_workflows.py` confere que os jobs do `ci.yml` são exatamente os que o gate exige, e que os workflows novos têm actions fixadas por SHA, nenhum segredo, as permissões mínimas, os gatilhos, as duas linguagens e nenhuma exceção escondida.
- O zizmor audita os dois arquivos, como todo workflow.
