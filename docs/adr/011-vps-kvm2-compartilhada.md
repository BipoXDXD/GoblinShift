# ADR 011. VPS KVM 2 da Hostinger, compartilhada com outro projeto do mesmo dono

- **Estado:** aceito (recursos confirmados pela medição da fatia 0, 2026-10-13)
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [3.6](../GoblinShiftPlan.md#s3-6), [3.8](../GoblinShiftPlan.md#s3-8), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [D17](../GoblinShiftReferencias.md#d-4), [D30](../GoblinShiftReferencias.md#d-6)

## Contexto

O GoblinShift é um projeto de portfólio com custo fixo de hospedagem. A mesma VPS vai hospedar também outro projeto do mesmo dono, cada um no seu domínio. O servidor é uma VPS KVM 2 da Hostinger (2 vCPU, 8 GB de RAM, 100 GB), Ubuntu 24.04 LTS, datacenter de Campinas (SP), com root completo, o que permite Docker, gVisor e filesystems dedicados.

## Decisão

O GoblinShift roda no KVM 2 compartilhado. O compartilhamento define três regras: o GoblinShift não assume a máquina inteira; divide as portas 80/443 com o outro projeto por meio de um proxy de borda (ADR 014); e usa usuário, diretórios e filesystems próprios (`pg` 10 GiB, `storage` 24 GiB, `exec-work` 6 GiB, `telemetry` 10 GiB). As regras de firewall são acrescentadas, nunca substituindo as do outro projeto ([3.8](../GoblinShiftPlan.md#s3-8)).

| Recurso | Como fica no KVM 2 compartilhado                                                                                |
|---------|-----------------------------------------------------------------------------------------------------------------|
| CPU     | Executor limitado a 1 vCPU; o outro vCPU fica para API, worker, manutenção, PostgreSQL, Nginx e o outro projeto |
| RAM     | Executor com até 2 GiB; a pilha do GoblinShift em repouso usa cerca de 385 MiB (medição abaixo)                 |
| Disco   | 50 GiB em filesystems de tamanho fixo; o restante fica para o sistema, imagens Docker e o outro projeto         |
| Upgrade | Para KVM 4 quando as métricas de CPU ou os SLOs pedirem; a Hostinger faz o upgrade sem reinstalar               |

## Alternativas descartadas

| Alternativa         | Motivo                                                                 |
|---------------------|------------------------------------------------------------------------|
| KVM 4 dedicado      | Custo de portfólio; os recursos medidos cabem com o executor em 1 vCPU |
| Uma VPS por projeto | Custo de portfólio; os recursos medidos cabem com o executor em 1 vCPU |

## Medição na VPS (fatia 0)

Medido em 2026-10-13 com 20 000 requisições sintéticas a cerca de 160 req/s; a medição completa está no [ADR 013](013-openobserve-telemetria.md).

| Medida                                 | Resultado                                                    |
|----------------------------------------|--------------------------------------------------------------|
| RAM da pilha em repouso (6 containers) | cerca de 385 MiB                                             |
| Pico de RAM sob carga                  | OpenObserve 246 MiB, Collector 56 MiB                        |
| Filesystems de tamanho fixo criados    | pg 10 GiB, storage 24 GiB, exec-work 6 GiB, telemetry 10 GiB |

## Consequências

- O isolamento entre os dois projetos depende do gVisor; o risco é aceito ([3.6](../GoblinShiftPlan.md#s3-6) lembra que nem container nem gVisor são garantia absoluta).
- O provisionamento do host é aditivo: não substitui nada que pertença ao outro projeto.
- Com 1 vCPU para o executor, o teto de 10 minutos por tentativa só é confirmado com medição na fatia 4 ([1.7](../GoblinShiftPlan.md#s1-7)).
- Se as métricas de CPU ou os SLOs pedirem, o upgrade para o KVM 4 não exige reinstalar, e este ADR é revisto.

## Como verificar

- `deploy/provision.sh` segue aditivo (não substitui regras de firewall nem diretórios do outro projeto) e cria os quatro filesystems de tamanho fixo; `df -h /srv/goblinshift/*` mostra os tamanhos acima.
- Na VPS, `docker stats --no-stream` mostra a RAM da pilha do GoblinShift em repouso; a referência é a medição de 2026-10-13 (cerca de 385 MiB).
- A partir da fatia 8, as métricas de sistema (CPU, memória, disco por filesystem) do Collector e os SLOs ficam no OpenObserve ([3.7](../GoblinShiftPlan.md#s3-7)); são elas que decidem o upgrade para o KVM 4.
- Na fatia 1, o teste de aceitação do ambiente prova os limites do executor (2 GiB, 64 processos, 6 GiB, sem rede, `runsc`); se algo falhar, a alternativa (outro plano de VPS, outro runtime) é decidida num ADR antes de seguir.
- Na fatia 4, a medição confirma o teto de 10 minutos por tentativa com 1 vCPU.
