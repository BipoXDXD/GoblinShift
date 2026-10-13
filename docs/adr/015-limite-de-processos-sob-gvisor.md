# ADR 015. Limite de processos do executor sob gVisor: RLIMIT_NPROC na receita, `--pids-limit` como rede de segurança

- **Estado:** aceito (medição da fatia 1, 2026-10-13)
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [3.6](../GoblinShiftPlan.md#s3-6), [3.8](../GoblinShiftPlan.md#s3-8), [3.9](../GoblinShiftPlan.md#s3-9)
- **Referências:** [D17](../GoblinShiftReferencias.md#d-4), [D30](../GoblinShiftReferencias.md#d-6)

## Contexto

O plano limita o executor a 64 processos, e o teste de aceitação exige que um fork bomb pare nesse número ([3.8](../GoblinShiftPlan.md#s3-8)). O perfil inicial usava `--pids-limit=64`. Na VPS, com o gVisor `release-20261005.0` na plataforma systrap, o primeiro teste de aceitação reprovou esse critério: o sandbox inteiro morreu depois de 16 processos da receita, com saída 2 e sem erro registrado pelo runtime, em vez de o `fork` falhar com `EAGAIN`.

O motivo é que o `--pids-limit` vale para o cgroup do sandbox no host, que contém as tarefas do próprio gVisor, e não só os processos da receita. Um experimento com a mesma sonda (forks com teto de 256) mediu o pico de tarefas do cgroup:

| Variante                         | Forks da receita | Como terminou            | Pico de tarefas no host |
|----------------------------------|------------------|--------------------------|-------------------------|
| Ocioso, `--pids-limit=64`        | 0                | normal                   | 31                      |
| `--pids-limit=64`                | 16               | sandbox morre (saída 2)  | 62                      |
| `--pids-limit=128`               | 48               | sandbox morre (saída 2)  | 123                     |
| `--pids-limit=512`               | 229              | morto (saída 137)        | 488                     |
| `--pids-limit=512` + `nproc=64`  | 63               | `EAGAIN`, saída normal   | 159                     |

O gVisor ocioso usa 31 tarefas, e cada processo da receita acrescenta cerca de 2. O RLIMIT_NPROC é aplicado pelo kernel do próprio gVisor, que recusa o fork com `EAGAIN` como o Linux faria.

## Decisão

Dois limites com papéis separados, no perfil fixo do `executor-ctl`:

- **`--ulimit nproc=64`**: o limite da receita, que conta processos e threads. O 65º recebe `EAGAIN`, e a receita continua viva para falhar com erro próprio.
- **`--pids-limit=256`**: só a rede de segurança das tarefas do sandbox no host. Fica acima do pico medido com a receita no limite (159), para nunca agir antes do `nproc`; se agir, o sandbox morre, mas o host continua protegido.

O UID do executor (61000) não existe no host e só roda uma tentativa por vez, então o RLIMIT_NPROC, que é contado por UID, vale para a tentativa inteira.

## Alternativas descartadas

| Alternativa                      | Motivo                                                                                                                                                                                                                   |
|----------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Manter `--pids-limit=64`         | Na prática, cerca de 16 processos da receita; estourar mata o sandbox com erro opaco                                                                                                                                     |
| Só `--pids-limit`, calibrado em ~160 | Um número só, mas estourar continua matando o sandbox em vez de dar `EAGAIN`, e a proporção de 2 tarefas no host por processo é detalhe interno do gVisor, que pode mudar entre versões sem aviso                  |

## Consequências

- O teste de aceitação passa a provar o `EAGAIN` no 64º processo, além de conferir os dois limites no `docker inspect`. Uma versão nova do gVisor que deixe de aplicar o RLIMIT_NPROC reprova o teste antes de chegar à produção.
- Os dois números ficam num lugar só, o perfil do `executor-ctl` (hoje `deploy/acceptance/executor_ctl.py`; na fatia 4, o serviço real). Se o `nproc` subir, a rede de segurança é recalculada: 31 + 2 × `nproc`, com folga.
- Uma receita com muitas threads (FFmpeg, por exemplo) conta threads no mesmo limite de 64; a medição da fatia 4 confirma que as receitas reais cabem.
- Também medido na fatia 1: o gVisor monta um `tmpfs` próprio em `/tmp`, mesmo com a raiz read-only. O que se escreve ali fica na memória do sandbox e conta nos 2 GiB, sem chegar ao disco do host. A área de trabalho das receitas continua sendo `/work`.

## Como verificar

- `deploy/acceptance/executor_ctl.py` tem `--ulimit=nproc=64` e `--pids-limit=256` no perfil fixo.
- Na VPS, `sudo python3 -B deploy/acceptance/run.py` mostra "fork bomb contido em 64 processos" com 63 processos criados e `EAGAIN`, e o perfil fixo sem divergências (`PidsLimit` 256, `ulimit nproc` 64).
