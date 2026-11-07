# ADR 045. Publicação em produção pelo Coolify: registro da troca e destino do caminho por SSH

- **Estado:** Aceito (2026-11-07). Este ADR não altera código, workflows, ADRs anteriores nem a VPS.
- **Plano:** [3.8](../GoblinShiftPlan.md#s3-8) (deploy e VPS)
- **Relacionado:** [ADR 004](004-container-gvisor-por-tentativa.md) (só o `executor-ctl` fala com o Docker pelo executor), [ADR 011](011-vps-kvm2-compartilhada.md) (VPS compartilhada), [ADR 022](022-backup-externo.md) (atualização de 2026-11-05: o backup saiu do checkout), [ADR 033](033-segredos-de-deploy-no-environment-production.md) (segredos do deploy no environment `production`), [ADR 034](034-main-e-root-na-vps-compartilhada.md) ("main = root" aceito), [ADR 041](041-ip-do-cliente-atras-da-cloudflare.md) (procedimento de leitura da configuração Git do Coolify)
- **Próximo passo:** os ADR 033 e 034 passam a "Substituído em parte" num PR próprio. Hoje eles continuam descrevendo o caminho anterior.

## Contexto

O ADR 022, na atualização de 2026-11-05, já registra a passagem da publicação para o Coolify, e o ADR 034 já leva a política do host aos JSON de `deploy/coolify/`. Falta um ADR que registre a decisão sobre a troca e o destino do caminho anterior. O histórico do Git mostra a troca: o `coolify.yml` entrou em 2026-10-21; o job `deploy` do `ci.yml` virou `if: "false"` em 2026-10-25; em 2026-11-05 o `deploy/deploy.sh` passou a só recusar e o backup saiu do checkout. O ADR 033 (segredos `VPS_SSH_KEY` e `VPS_KNOWN_HOSTS`) e as barreiras do ADR 034 ("main = root" aceito) continuam descrevendo o caminho antigo. O README (seção "Publicação em produção") e o Plano 3.8 já descrevem o Coolify como o caminho de produção e dizem que o `deploy/rollout-api.sh` não faz parte dele.

### O que o deploy faz hoje

Levantado de `.github/workflows/coolify.yml`, `deploy/coolify/` e dos testes. Nada foi consultado na VPS.

1. **Gatilho.** `coolify.yml` roda quando o workflow `CI` termina com sucesso num push na `main` do próprio repositório (`workflow_run`), ou à mão (`workflow_dispatch`) com o SHA completo. Pull request só roda os testes das ferramentas de release.
2. **Job `gates`.** Faz checkout da `main` e roda `deploy/coolify/release/gate.py`: o commit precisa ser o HEAD atual da `main` (um SHA superado encerra sem publicar), a execução do CI precisa ser `ci.yml`, push, `main`, e os 10 jobs obrigatórios precisam estar verdes. Os jobs legados "Deploy na VPS" e "Jornadas na VPS (pós-deploy)" precisam constar como `skipped`; qualquer job a mais ou a menos reprova.
3. **Job `deploy`.** Roda no environment `production`, com `contents`, `actions` e `packages` só de leitura, no commit exato. Escaneia com o Trivy (HIGH e CRITICAL, `--ignore-unfixed`) as imagens `goblinshift`, `goblinshift-web` e `goblinshift-executor` do GHCR, já resolvidas por digest `linux/amd64`. O `send.sh` baixa as imagens com `crane`, monta um tar OCI e o envia por SSH, com o comando `release <sha de 40 hex>`, à conta restrita do projeto (variável `COOLIFY_RELEASE_TARGET`), com `known_hosts` fixado.
4. **Receptor na VPS** (`/opt/coolify-release/receiver.py`, root, instalado pelo administrador, fora do Git). Aceita só `release <sha>` e recusa se os bytes dele diferirem do hash que o remetente declara. Valida que as três imagens são digests imutáveis dos três repositórios permitidos, importa no daemon, lê a configuração do projeto em `/etc/coolify-release`, grava no Coolify `GS_APP_IMAGE`, `GS_WEB_IMAGE` e `GS_EXECUTOR_IMAGE` pela API local, acrescenta o digest do executor à política root-owned que o `executor-ctl` relê a cada `start`, e inicia o recurso. Só registra sucesso depois de conferir identidade da imagem, saúde e prontidão. Não há downgrade automático de schema.
5. **O que o Coolify executa.** `deploy/coolify/app.json` e `infra.json`: imagens por digest, `pull_policy: never`, tetos de memória, CPU e processos, binds de dados limitados a `storage`, `exec-work`, `pg/data`, `telemetry/data` e `/run/goblinshift` (mais leituras só-leitura para o alerta de disco), segredos em arquivo. O gateway (`gateway.json`, `Caddyfile`, `sites/`) é outro recurso e não passa pela release (ADR 041).
6. **Depois.** `e2e-producao.yml` roda ao fim de cada release (e toda semana) com a chave de teste.

### Credenciais: o que existe e onde

| Credencial | Onde o código a lê | Onde deve ficar | O que alcança |
|---|---|---|---|
| `COOLIFY_RELEASE_SSH_KEY`, `COOLIFY_RELEASE_KNOWN_HOSTS` (segredos) e `COOLIFY_RELEASE_TARGET` (variável) | `coolify.yml`, job `deploy` | Environment `production`, restrito à `main` (`deploy/coolify/release/README.md`) | Publicar `release <sha>` na conta restrita do projeto. Na homologação em laboratório, a conta tem forced command por projeto, `sudo` exato e nenhum grupo `docker`; a instalação real não está no repositório |
| `GITHUB_TOKEN` com `packages: write` | `ci.yml`, jobs de imagem (só push na `main`) | Efêmero | Publicar as três imagens no GHCR |
| `GITHUB_TOKEN` com `packages: read` | `coolify.yml`, job `deploy` | Efêmero | Ler as imagens para escanear e transportar |
| `E2E_VPS_API_KEY` | `e2e-vps.yml`, `e2e-producao.yml` | Environment `production` (ADR 033, atualização de 2026-11-05) | Chave de API da conta de teste (`files:*`, `runs:*`), válida até 2027-02-03 |
| Token da API do Coolify | Receptor, na VPS | VPS, só root, API limitada a IPs locais (`release/README.md`) | Controla os recursos do Coolify; não está no GitHub |
| Receptor, configuração do projeto e política de digests do executor | Receptor e `executor-ctl` | VPS, administração (`/opt/coolify-release`, `/etc/coolify-release`, `/var/lib/coolify-release`); a política do executor é root, modo 0600 | Não são alteráveis pela `main`: mudar o receptor no Git não atualiza o executável da VPS |
| **Legado:** `VPS_SSH_KEY`, `VPS_KNOWN_HOSTS` (segredos), `VPS_HOST`, `VPS_USER` (variáveis) | Só o job `deploy` do `ci.yml`, que nunca roda | Environment `production` (ADR 033) | Caminho anterior; a retirada segue em procedimento próprio |

A posição real dos segredos é configuração do GitHub e da VPS. O código não a prova.

### O que um push na `main` alcança

| Vetor | O que a `main` alcança | Evidência | Situação |
|---|---|---|---|
| Release pelo receptor | Três digests dos repositórios `goblinshift*` do GHCR e o `start` do recurso do projeto. Não alcança shell, daemon do Docker, Compose, outros recursos nem o código root da VPS | `receiver.py` (`parse_request`, `validate_release`, `deploy`); regex da conta em `send.sh` | Não é root |
| Código dentro das imagens | Containers do app com bind de escrita em `storage`, `exec-work` e `/run/goblinshift` (socket do `executor-ctl`, só `worker` e `maintenance`) e segredos em arquivo (`db_password`; OAuth, CSRF e cookie na `api`). Sem `privileged`, `cap_add` nem socket do Docker | `app.json`; `tests/test_compose_host_policy.py` passa `app.json`, `infra.json` e `gateway.json` pela política do host (`coolify_policy_violations` em `tests/compose_host_policy.py`) e reprova `privileged`, `cap_add`, socket do Docker e bind fora da allowlist | Não é root, e o teste obriga isso. **Sem teste nos JSON:** a restrição do socket do `executor-ctl` a `worker` e `maintenance` (ver abaixo) |
| Imagem do executor | O digest novo entra na política root e o `executor-ctl` passa a poder iniciá-lo, sempre com `--network=none`, `--read-only`, `--cap-drop=ALL`, `no-new-privileges` e `runsc` | `deploy/executor_ctl/profile.py` e `config.py`; `receiver.py` (`append_goblin_policy`) | Sair do sandbox exige quebrar o gVisor |
| Workflows | Qualquer job novo na `main` com `environment: production` lê a chave de release e pode publicar **qualquer** digest dos três repositórios, inclusive antigos, sem o Trivy nem os gates, que rodam no workflow e não no receptor. Hoje quatro jobs declaram esse environment | `receiver.py` não verifica que a imagem veio do CI nem que corresponde ao commit; `grep environment:` nos workflows | Não é root (o forced command só publica), mas desfaz os gates |
| Definições do Coolify lidas do Git | Se os recursos `app` e `infra` seguirem a `main` (ou tiverem auto-deploy), um merge troca as definições em execução. A política do host reprova na CI o erro honesto (`privileged`, bind de `/`), mas não detém quem altera o teste no mesmo commit (ADR 034) | `release/README.md` diz que a configuração fica "fixada no commit de infraestrutura revisado". O ADR 041 mostra que o recurso do gateway pode estar em `HEAD` | **Não verificável pelo código** |
| Caminho legado | A retirada do caminho anterior segue em procedimento próprio | Procedimento próprio | Fora deste ADR |

O repositório também não mostra: como o Coolify está exposto e autenticado, se o receptor instalado é o de hash `receiver-release.json`, nem quais privilégios a conta de release tem na VPS real.

## Reavaliação do risco "main = root"

O ADR 034 aceitou o risco apoiado nas barreiras abaixo (a separação do executor, também citada lá, não mudou). O que sobrou de cada uma:

| Barreira do ADR 034 | No modelo do Coolify |
|---|---|
| Acesso da CI restrito ao `deploy.sh` | Passou para uma chave presa ao receptor, que só aceita `release <sha>`. O caminho anterior segue em procedimento próprio (linha "Caminho legado") |
| Pedido validado (`sha` na `main`, tag do próprio commit) | `gate.py`: commit é o HEAD da `main`, CI de push na `main`, 10 jobs verdes. O receptor valida o formato e o hash do receptor, mas não a procedência da imagem |
| Só a `main` chega ao environment `production` | Igual (ADR 033), mas quatro jobs usam esse environment e a chave de release serve a qualquer job novo na `main` |
| Fitness function do Compose de produção (`tests/test_compose_host_policy.py`) | **Cobre a produção.** O teste lê `compose.yaml` e `compose.prod.yaml` e também `app.json`, `infra.json` e `gateway.json`, depois do `docker compose config`, pela `coolify_policy_violations`, que ainda exige imagem por digest e proíbe `build` (ADR 034). Roda no job `Verificações`, que o `gate.py` exige. Como `test_compose_docker_socket.py` só lê YAML, nos JSON o socket do Docker fica coberto pela allowlist de binds, e a restrição do socket do `executor-ctl` a `worker` e `maintenance` fica sem teste: a política aceita `/run/goblinshift` em qualquer serviço |
| CODEOWNERS em `deploy/`, `docker/gateway/`, `compose*.yaml` e `.github/workflows/` | Igual. Continua sem exigir segundo revisor |

Conclusão, com o que o código mostra:

- **Pelo caminho de release, a `main` deixa de ser root.** Ela publica imagens por digest numa conta restrita, e as imagens rodam em containers sem privilégio, com binds e segredos limitados. O código root da VPS (receptor, `executor-ctl`, configuração, token do Coolify) não muda por push.
- **O "main = root" do ADR 034 só continua valendo pelo que o código não consegue excluir:** as definições do Coolify lidas do Git (se os recursos seguirem a `main`), a conferir no passo 1. O caminho anterior segue em procedimento próprio.
- **A defesa do host cobre os JSON do Coolify, com duas lacunas:** a política não restringe qual serviço monta o socket do `executor-ctl`, regra que hoje só tem teste nos YAML; e os gates e o scan do Trivy valem para o workflow, não para quem tem a chave de release.
- **Um componente novo entra no raio de explosão:** o próprio Coolify (painel, API, acesso ao daemon), cuja exposição o repositório não descreve.

## Alternativas

| Alternativa | Prós | Contras e custo |
|---|---|---|
| **A. Registrar o Coolify como caminho oficial e aposentar o legado** (job `deploy`, `deploy.sh`, `release.sh` e segredos) | Encerra o caminho anterior. Um caminho só para manter e testar. Remove o acoplamento entre um job morto e o gate. ADRs e código passam a dizer a mesma coisa | O gate exige os dois jobs legados como `skipped` por nome exato: remover o job sem mudar `gate.py` e os testes **trava todas as releases** (falha fechada). Passos manuais fora do repositório (GitHub e VPS). `workflow_dispatch` de um SHA anterior à remoção passa a ser recusado. Os scripts de apoio (`compose.sh`, `rollout-api.sh`, `executor-image.sh`, `release-env.sh`, `app-secrets.sh`, `storage-mark.sh`) têm testes e referências em `compose.yaml`, `compose.prod.yaml`, `scripts/check-zero-downtime.sh` e README, então a remoção é por inventário, não em bloco |
| **B. Manter o legado como plano de contingência** | Um segundo caminho de CI se o Coolify ou o receptor falharem | Hoje não é uma contingência utilizável: o `deploy.sh` recusa, o gate exige o job `skipped` e o `release.sh` recria a pilha fora do Coolify, que é dono dos containers `goblinshift-*` (o próprio cabeçalho do script manda não rodá-lo). Torná-la real exige reabrir o gate, o `deploy.sh` e os ADR 033 e 034, manter dois caminhos testados e sustentar um fallback que ninguém exercita |
| **C. Só registrar, sem remover** | Sem risco para as releases, sem passo manual, PR trivial | Mantém o job morto e os segredos. O ADR 034 continuaria aceitando risco sobre um caminho que ninguém usa nem testa. Não encerra o caminho anterior |

## Decisão

Alternativa A. Os próximos passos seguem a ordem abaixo; cada um vale por si e o dono pode parar entre eles.

1. **Fora do repositório** (dono, antes de qualquer PR de código): retirar o caminho anterior, em procedimento próprio; apagar `VPS_SSH_KEY` e `VPS_KNOWN_HOSTS` em todos os níveis (repositório e environment) e as variáveis `VPS_HOST` e `VPS_USER`; ler `git_commit_sha` e `is_auto_deploy_enabled` dos recursos `app`, `infra` e `gateway` pelo procedimento do ADR 041. Como o job legado nunca roda, nada disso afeta as releases.
2. **PR de código, depois do passo 1:** remover os jobs `deploy` e `vps-smoke` do `ci.yml`, esvaziar `LEGACY_SKIPS` em `gate.py` e ajustar `test_gate.py` e `tests/test_ci_deploy_workflow.py` no mesmo PR, remover `deploy/deploy.sh`, `deploy/release.sh` e o bloco da chave da CI do `provision.sh`. Os scripts de apoio saem só depois de inventário, sem apagar o que `compose.prod.yaml`, a aceitação ou a política do host ainda leiam. A conta do backup mantém o acesso de que o `docker exec` precisa: mudar isso é outra decisão.
3. **Reforço, qualquer que seja a alternativa:** a política do host já cobre `deploy/coolify/app.json`, `infra.json` e `gateway.json` (ADR 034). Falta levar aos JSON a regra de `tests/test_compose_docker_socket.py` que limita o socket do `executor-ctl` a `worker` e `maintenance`.
4. **Documentação:** marcar os ADR 033 e 034 como substituídos em parte, num PR próprio.

## Consequências

- **Fica:** um único caminho de publicação, com gate de CI, scan e um receptor root, fora do Git, que só aceita `release <sha>`. O "main = root" deixa de ser o risco aceito por padrão e passa a depender só das conferências do passo 1, que as fecha ou confirma.
- **Custo:** perde-se um caminho de emergência que, na prática, já não funciona. A recuperação passa a ser só administrativa (reconciliação e rollback em `deploy/coolify/release/ISOLATION.md`), com a VPS e o painel nas mãos do operador.
- **Custo:** o acoplamento do gate aos nomes exatos dos jobs é frágil nos dois sentidos. Mudar `ci.yml` e `gate.py` no mesmo PR resolve a `main`, mas um SHA antigo, com os jobs legados, deixa de ser publicável por `workflow_dispatch`.
- **Risco que continua aceito:** quem põe um workflow novo na `main` lê a chave de release e pode publicar qualquer digest dos repositórios do projeto sem os gates. Fechar isso pede amarrar o receptor à procedência da imagem (por exemplo, um atestado de procedência) ou reviewers no environment. Fica fora deste ADR.
- **Risco que continua aceito:** o receptor e o `executor-ctl` são código root instalado por procedimento administrativo, não por release. O Coolify tem acesso ao daemon e sua exposição não está documentada aqui.
- **Gatilhos para revisar:** a abertura ao público ([fatia 8](../GoblinShiftPlan.md#s3-9)); um segundo mantenedor ou automação com permissão de merge na `main`; o Coolify passar a ler definições da `main` sem fixar o commit; outro workflow passar a usar a chave de release.

## Compliance

- `gh secret list` (repositório) e `gh secret list --env production` não trazem `VPS_SSH_KEY` nem `VPS_KNOWN_HOSTS`; `gh variable list` não traz `VPS_HOST` nem `VPS_USER`. O environment traz só `COOLIFY_RELEASE_SSH_KEY`, `COOLIFY_RELEASE_KNOWN_HOSTS` e `E2E_VPS_API_KEY`.
- `gh api repos/{owner}/{repo}/environments/production/deployment-branch-policies` lista só `main` (ADR 033).
- A retirada do caminho anterior está concluída e registrada no procedimento próprio.
- Para cada recurso do Coolify do projeto, `git_commit_sha` é um commit fixo e `is_auto_deploy_enabled` é falso (leitura no ADR 041). Registrar o resultado e a data numa atualização deste ADR.
- Depois do PR de código: `grep -rn "VPS_SSH_KEY\|VPS_KNOWN_HOSTS\|deploy/deploy.sh" .github deploy tests` não encontra nada; `python3 -m unittest discover -s deploy/coolify/release -v` e `uv run pytest tests/test_ci_deploy_workflow.py` passam com o novo conjunto de jobs; um teste novo reprova qualquer referência a `secrets.VPS_`.
- `uv run pytest tests/test_compose_host_policy.py -q` passa: a política do host roda sobre os três JSON do Coolify e reprova `privileged`, `cap_add`, socket do Docker e bind fora da allowlist injetados neles.
- Depois do reforço: um teste reprova, nos JSON, a montagem de `/run/goblinshift` por serviço que não seja `worker` ou `maintenance`.

## Resumo

**A**, na ordem dos passos 1 a 4. **Motivo:** o Coolify já é o único caminho de produção (README, `deploy.sh`, `gate.py`), enquanto B mantém um fallback que nem funciona e C mantém um caminho que ninguém usa nem testa. **Custo principal:** mexer no acoplamento entre os jobs legados e o gate sem travar as releases, mais os passos manuais fora do repositório (GitHub e VPS) que só o dono pode fazer.
