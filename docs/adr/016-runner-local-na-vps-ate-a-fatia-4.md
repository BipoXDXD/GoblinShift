# ADR 016. Runner local em subprocesso na VPS até a fatia 4

- **Estado:** Substituído (Superseded) na fatia 4, 2026-10-14: em produção só existe o `Runner` do `executor-ctl` (container gVisor por tentativa, [ADR 004](004-container-gvisor-por-tentativa.md)); o worker e a manutenção recusam subir com `GS_RUNNER=local`, sem confirmação que reabra a porta, e `GS_ALLOW_LOCAL_RUNNER` deixou de existir ([3.9](../GoblinShiftPlan.md#s3-9), fatia 4). Antes: aceito (decisão do usuário, fatia 3, 2026-10-13), provisório
- **Plano:** [3.2](../GoblinShiftPlan.md#s3-2), [3.6](../GoblinShiftPlan.md#s3-6), [3.9](../GoblinShiftPlan.md#s3-9)
- **Relacionado:** [ADR 004](004-container-gvisor-por-tentativa.md) (ajustado por este ADR)

## Contexto

O critério de pronto da fatia 3 exige que um script com `curl` execute o CSV Janitor de ponta a ponta **na VPS** ([3.9](../GoblinShiftPlan.md#s3-9)). O executor real (`executor-ctl` de produção, container gVisor por tentativa, área de trabalho de tamanho fixo) só chega na fatia 4. O ADR 004 e o plano previam o `Runner` local, em subprocesso, apenas fora de produção, o que deixaria a fatia 3 sem como fechar o critério na VPS.

## Decisão

O `Runner` local roda o executor (`python -m goblinshift.processing.executor <work_dir>`) em subprocesso do host e pode ser usado na VPS **até a fatia 4**, com duas travas:

- **Só por configuração explícita.** A escolha do Runner local em produção exige a confirmação `allow_local_runner` (`GS_ALLOW_LOCAL_RUNNER`), desligada por padrão. Sem ela, o worker em produção não sobe com o Runner local.
- **Serviço continua fechado.** Não há login nem conta aberta; o acesso é por chave de API emitida pelo administrador com `gs-admin` ([3.9](../GoblinShiftPlan.md#s3-9), fatia 3).

## Alternativas descartadas

| Alternativa                                     | Motivo                                                                                                      |
|-------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| Fatia 3 pronta só localmente (Runner local só fora de produção) | Contraria a regra de 3.9: uma fatia está pronta quando o critério passa na VPS, não só localmente |

## Consequências

- Até a fatia 4, o CSV de quem tem chave roda **sem gVisor**: é um subprocesso comum do host, sem os limites e o isolamento de [3.6](../GoblinShiftPlan.md#s3-6). Risco aceito, porque só o administrador tem chave e a receita é Python puro (biblioteca padrão). Executor sem rede só existe a partir da fatia 4.
- O ADR 004 continua valendo para a fatia 4 em diante. A frase "o `Runner` local só existe fora de produção" passa a valer a partir da fatia 4.
- O serviço não abre ao público antes da fatia 8, então a janela de exposição é a da fatia 3 até a fatia 4, com um só usuário.
- O contrato do `Runner` ([3.2](../GoblinShiftPlan.md#s3-2)) não muda: a troca pelo `executor-ctl` não toca o worker nem a coleta.

## Como verificar

- Na fatia 3, o script com `curl` na VPS executa o CSV Janitor de ponta a ponta com o Runner local.
- O worker em produção sem `GS_ALLOW_LOCAL_RUNNER` recusa iniciar com o Runner local (teste do laço do worker).
- Na fatia 4, o Runner local e a confirmação são removidos do código de produção; o serviço recusa iniciar com ele, como o ADR 004 já prevê, e este ADR passa a **Substituído**.
