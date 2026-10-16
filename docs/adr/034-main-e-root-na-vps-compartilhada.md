# ADR 034. O deploy controla o Docker da VPS: "main = root" aceito por escrito

- **Estado:** Aceito (decisão do usuário, 2026-10-15).
- **Plano:** [3.8](../GoblinShiftPlan.md#s3-8) (deploy e VPS)
- **Relacionado:** [ADR 004](004-container-gvisor-por-tentativa.md) (só o `executor-ctl` fala com o Docker pelo executor), [ADR 011](011-vps-kvm2-compartilhada.md) (VPS compartilhada com o StillOn), [ADR 014](014-proxy-de-borda-caddy.md) (rede `edge` compartilhada), [ADR 022](022-backup-externo.md) (backup por `docker exec`), [ADR 033](033-segredos-de-deploy-no-environment-production.md) (segredos do deploy no environment `production`)

## Contexto

O `deploy/provision.sh` põe o usuário `goblinshift` no grupo `docker`. Quem fala com o daemon do Docker monta `/` do host num container, então esse usuário equivale a root na VPS, inclusive sobre os containers do StillOn: o daemon é um só e não é rootless (ADR 011).

Quem usa o grupo de fato:

| Caminho                        | O que faz com o Docker                                                                                       |
|--------------------------------|--------------------------------------------------------------------------------------------------------------|
| Deploy pela CI                 | Forced command → `deploy/deploy.sh` → `deploy/release.sh` **do commit implantado** → `deploy/compose.sh` (`pull`, `run`, `up`, `exec`), `deploy/rollout-api.sh` (`stop`, `rm`) e `image prune` |
| Backup diário (ADR 022)        | `docker exec` no PostgreSQL, pela unit `goblinshift-pg-backup.service` com `User=goblinshift`                 |
| Operador                       | `sudo -u goblinshift deploy/compose.sh …`; já tem sudo, então o grupo não lhe acrescenta nada                 |

O worker e a manutenção não têm o socket do Docker (ADR 004). O `executor-ctl` é root, via systemd, e expõe só quatro operações ao grupo `gs-exec`.

O risco real não é o grupo em si. O `release.sh` e os arquivos Compose vêm do commit implantado, e um Compose com `privileged: true` ou `volumes: ["/:/host"]` basta para tomar o host. Por isso, **quem põe código na `main` é root na VPS compartilhada**. Tirar o grupo e dar sudo a um script dentro do checkout não muda isso, porque o `goblinshift` é dono de `/srv/goblinshift/app` e reescreveria o script. Fora a chave da CI, presa ao forced command, ninguém chega a um shell como `goblinshift`.

## Decisão

Aceitar o risco "main = root na VPS compartilhada", com as barreiras que já existem e dois reforços baratos.

Barreiras que já existem:

- **Chave da CI presa ao deploy.** `command="…/deploy/deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty` em `authorized_keys`.
- **Pedido validado.** O `deploy.sh` só aceita `deploy <sha de 40 hex> <sha-12 hex>`, exige que a tag seja a do próprio commit e que o commit já esteja na `main` (`git merge-base --is-ancestor`).
- **Só a `main` passa pela CI até o deploy.** O job `deploy` depende de todas as verificações e roda no environment `production`, com os segredos da VPS só nele (ADR 033).
- **Separação do executor.** O conteúdo de terceiros roda sob gVisor, criado pelo `executor-ctl`, e nenhum Compose monta o socket do Docker (ADR 004, `tests/test_compose_docker_socket.py`).

Reforços deste ADR:

1. **Fitness function do Compose de produção** (`tests/test_compose_host_policy.py`, com a política em `tests/compose_host_policy.py`). Roda o `docker compose config` real sobre `compose.yaml` + `compose.prod.yaml`, com os caminhos que o `deploy/compose.sh` exporta na VPS. Reprova:
   - `privileged`, `cap_add`, `devices`, `volumes_from` e `cgroup_parent`;
   - `pid`, `network_mode`, `ipc`, `userns_mode`, `uts` ou `cgroup` iguais a `host` ou `container:…`, porque o namespace de outro container alcança o StillOn;
   - `security_opt` que desliga confinamento (`seccomp`, `apparmor` ou `systempaths` `unconfined`, e `label:disable`);
   - bind mount fora da allowlist. Para escrita: `storage`, `pg/data`, `telemetry/data`, `exec-work` e `/run/goblinshift`. Só leitura: `pg`, `storage`, `telemetry` e arquivos em `<repositório>/docker/`;
   - outros tipos de montagem, volume nomeado `external` ou com `driver_opts`;
   - secret fora de `/srv/goblinshift/secrets/` e config fora de `<repositório>/docker/`.
2. **CODEOWNERS** em `deploy/`, `compose*.yaml` e `.github/workflows/`, com `@BipoXDXD`, para que toda mudança nesses caminhos peça a revisão do dono.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| **B.** Deploy por wrapper de dono root fora do checkout, com `sudo` restrito, policy aplicada no próprio host antes do `up` e o `goblinshift` fora do grupo `docker` | É a única que reduz "main = root" para "main = o que a policy permite". Custa caro: a lógica de deploy sai do repositório e só muda por reprovisionamento, perdendo a propriedade de que uma mudança no deploy vale no próprio commit (`deploy/deploy.sh`). A policy precisa ser completa, porque um furo devolve o root. O backup e o `pg-restore-check.sh` precisariam de outro wrapper. Fica como próximo passo nos gatilhos abaixo |
| **C.** Docker rootless para o `goblinshift` | Quebra a rede externa `edge` do proxy de borda (ADR 014), que vive no daemon rootful. gVisor e limites de pids e memória em rootless exigem delegação de cgroup v2 e refazer a aceitação dos ADR 004 e 015. Reescreve o `executor-ctl` |
| Tirar o grupo e dar sudo a um script do checkout | Não protege: o `goblinshift` é dono do checkout e reescreve o script |

## Consequências

- Um commit malicioso ou um engano na `main`, um token do GitHub comprometido ou um PR mergeado sem revisão continuam valendo root na VPS, com acesso aos containers e dados do StillOn. Risco aceito: projeto de portfólio, um só mantenedor, serviço fechado até a abertura (ADR 023).
- O fitness test pega o **erro honesto** (um `privileged` ou um bind de `/` copiado de um exemplo). Não detém um atacante, que altera o teste no mesmo commit. O mesmo vale para o CODEOWNERS: com um só dono, ele marca os caminhos sensíveis e pede revisão, mas não exige um segundo par de olhos. Ligar "Require review from Code Owners" travaria os PRs do próprio dono.
- Mudança legítima que precise de algo fora da allowlist (outro bind, outra capability) muda a política em `tests/compose_host_policy.py` no mesmo PR, citando este ADR.

### Gatilhos para revisar e ir para a alternativa B

- A abertura ao público ([fatia 8](../GoblinShiftPlan.md#s3-9)): contas de terceiros passam a depender da integridade do host.
- O StillOn passar a guardar dado sensível de terceiros: o raio de explosão de um deploy comprometido passa a incluir esse dado.
- Um segundo mantenedor ou automação com permissão de merge na `main`.

## Como verificar (Compliance)

- `uv run pytest tests/test_compose_host_policy.py` passa no job "Verificações" do CI (o runner `ubuntu-24.04` traz o Docker com o plugin Compose; o `docker compose config` não precisa do daemon). Um `privileged: true` injetado num Compose extra reprova o teste (`test_privileged_injetado_no_compose_de_producao_e_reprovado`).
- `.github/CODEOWNERS` cobre `/deploy/`, `/compose*.yaml` e `/.github/workflows/`.
- Revisão de código: uma mudança em `tests/compose_host_policy.py` cita este ADR e explica por que a allowlist cresceu.
- Na VPS, `id goblinshift` mostra os grupos `docker` e `gs-exec`, e nenhum outro usuário além dele está no grupo `docker` (`getent group docker`).
