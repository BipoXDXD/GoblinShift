# ADR 005. Área do executor em filesystem de tamanho fixo

- **Estado:** aceito
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [3.6](../GoblinShiftPlan.md#s3-6), [3.8](../GoblinShiftPlan.md#s3-8), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [D17](../GoblinShiftReferencias.md#d-4)

## Contexto

O executor recebe 6 GiB de área de trabalho (temporários + saídas), e os limites de RAM e CPU não substituem um limite de disco. A cota precisa ser efetiva dentro do sandbox, sem consumir a RAM do próprio executor e sem arriscar o banco, o storage ou o sistema da VPS, que é compartilhada com outro projeto.

## Decisão

A área de trabalho é um filesystem ext4 de tamanho fixo (6 GiB, o valor de 1.7), criado como imagem e montado por loop no host em `/srv/goblinshift/exec-work`. Como só existe uma tentativa física por vez, o filesystem inteiro é a cota da tentativa: encher o disco falha com `ENOSPC` dentro do sandbox. O `executor-ctl` recusa `start` se a área não estiver vazia, e ela só é limpa depois de a coleta ser persistida. Dentro do container, `RLIMIT_FSIZE` limita o tamanho de cada arquivo.

## Alternativas descartadas

| Alternativa     | Motivo                                                      |
|-----------------|-------------------------------------------------------------|
| `--storage-opt` | O Docker só aplica `--storage-opt size` em XFS com `pquota` |
| tmpfs           | Um tmpfs com teto consumiria a RAM do próprio executor      |

## Consequências

- Encher a área de trabalho não afeta o banco, o storage nem o sistema: cada um tem o seu filesystem de tamanho fixo ([3.8](../GoblinShiftPlan.md#s3-8)).
- A cota por tentativa depende de haver uma tentativa física por vez ([1.7](../GoblinShiftPlan.md#s1-7)).
- A área não é limpa antes de a coleta ser persistida; quem pede `start` sobre uma área suja é recusado.
- O limite precisa funcionar no ambiente real: o teste de aceitação do ambiente prova o `ENOSPC` aos 6 GiB no próprio servidor.

## Como verificar

- `deploy/provision.sh` declara `[exec-work]=6G`, cria a imagem com `fallocate`, formata com `mkfs.ext4` e monta por loop (`/etc/fstab`); na VPS, `findmnt /srv/goblinshift/exec-work` mostra ext4 e `df -h` mostra 6 GiB.
- O teste de aceitação do ambiente ([3.8](../GoblinShiftPlan.md#s3-8)) escreve além de 6 GiB dentro do executor e confirma `ENOSPC`. Passa a valer na fatia 1 (protótipo do sandbox) e continua exigido na fatia 4.
- Na fatia 4, um teste do `executor-ctl` confirma que `start` com a área não vazia é recusado.
