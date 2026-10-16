# ADR 033: segredos de deploy no environment `production`, só para a `main`

## Status

Aceito.

## Contexto

O job `deploy` do `ci.yml` entra na VPS por SSH com a chave `VPS_SSH_KEY` (a chave da CI só
executa `deploy/deploy.sh`, por forced command). Os segredos `VPS_SSH_KEY` e `VPS_KNOWN_HOSTS`
estavam no nível do repositório, o que os deixa visíveis a qualquer workflow de qualquer branch,
inclusive um workflow novo num branch ou PR do próprio repositório.

Alternativas consideradas:

1. **Segredos no repositório (estado anterior).** Simples, mas qualquer workflow que um commit
   no repositório crie recebe a chave da VPS. O único freio é o `if` do job, que o mesmo commit
   pode alterar.
2. **Segredos no environment `production` com política de branches "só `main`".** Um job só
   lê segredo de environment se declarar `environment: production`, e o GitHub recusa o job num
   ref fora da política. Custo: configuração fora do repositório (política e segredos), que o
   código não consegue garantir.
3. **Required reviewers no environment.** Aprovação manual a cada deploy. Para um dev solo
   que já revisa o PR antes do merge, só atrasa o deploy sem proteger mais.

## Decisão

Opção 2. `VPS_SSH_KEY` e `VPS_KNOWN_HOSTS` existem apenas no environment `production`, com
política de branches personalizada contendo só `main`. O job `deploy` roda somente em `push`
na `main`, declara `environment: production` e `permissions: {}`. `VPS_HOST` e `VPS_USER`
continuam variáveis do repositório: o `if` do job precisa de `vars.VPS_HOST` antes de o
environment existir, e nenhum dos dois é segredo.

## Consequências

- Um workflow em outro branch ou PR não lê a chave da VPS: ou não declara o environment
  (e o segredo vem vazio) ou o declara e o GitHub recusa o ref.
- A política e a posição dos segredos são configuração do GitHub: o teste
  `tests/test_ci_deploy_workflow.py` garante só o lado do código (condição do job, environment,
  permissões, segredos lidos por `secrets.`).
- Compliance: `gh api repos/BipoXDXD/GoblinShift/environments/production/deployment-branch-policies`
  lista só `main`, e `gh secret list` (repositório) não traz `VPS_SSH_KEY` nem `VPS_KNOWN_HOSTS`.
