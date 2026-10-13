# ADR 006. Agregado Run e a transação multiagregado da publicação

- **Estado:** aceito
- **Plano:** [3.2](../GoblinShiftPlan.md#s3-2), [3.4](../GoblinShiftPlan.md#s3-4), [3.5](../GoblinShiftPlan.md#s3-5), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [B13](../GoblinShiftReferencias.md#l-4) (cap. 5)

## Contexto

Quando o executor termina, a publicação precisa tornar visíveis as saídas e o estado terminal da execução. Isso toca dois agregados: a `Run` (de `runs`) e os `Artifact` (de `files`). Gravar cada um na sua transação permitiria publicar só parte das saídas.

## Decisão

`Run` é a raiz do agregado de execução: `Attempt`, `RunInput` e `RunOutput` são entidades internas e só mudam por operações de `runs` sobre a Run travada (`RunRepository.get_for_update`). `Upload` e `Artifact` pertencem a `files` e referenciam um `StoredObject` por ID. Como exceção pragmática, a publicação grava na mesma transação curta o estado terminal da Run, os `RunOutput` e os `Artifact`. O `UPDATE` da Run é condicionado a `generation = :gen`, `status = 'running'`, ausência de intenção de cancelamento e `now() <= deadline_at`, com o relógio do PostgreSQL dentro da mesma transação.

## Alternativas descartadas

| Alternativa                                      | Motivo                                                                    |
|--------------------------------------------------|---------------------------------------------------------------------------|
| Publicar saídas e estado em transações separadas | Publicação parcial é pior que acoplar dois agregados numa transação curta |

## Consequências

- Se o `UPDATE` não afetar a linha, a publicação não aconteceu, e o motivo (cancelamento, prazo ou outra geração) é relido do banco. Por isso `canceled` nunca tem `RunOutput`.
- Os objetos são gravados antes do commit como candidatos privados, em chaves `runs/{run_id}/g{generation}/…`. Se o resultado do commit for incerto, reler por identificadores estáveis antes de apagar candidatos ou repetir a publicação.
- Unicidade e geração impedem publicação lógica duplicada; não prometem que o cálculo físico ocorrerá exatamente uma vez.
- Publicação e cancelamento disputam a mesma linha da Run. A ordem de locks é Run, depois Attempt; as reservas são liberadas numa transação seguinte, para não travar capacidade depois da Run.
- A transação é curta: esperas de rede e codecs acontecem fora dela.

## Como verificar

- Na fatia 3, os testes de concorrência com PostgreSQL real passam: escrita com geração vencida afetando zero linhas, publicação contra cancelamento e publicação contra prazo vencido ([3.7](../GoblinShiftPlan.md#s3-7)).
- Na fatia 3, a máquina de estados do Hypothesis ([3.5](../GoblinShiftPlan.md#s3-5)) confirma que `canceled` nunca tem `RunOutput` e que estado terminal nunca muda.
- Na fatia 4, os testes de caos (SIGKILL e SIGSTOP além do lease em cada fronteira do protocolo) passam na VPS sem publicação duplicada.
- O import-linter ([3.2](../GoblinShiftPlan.md#s3-2)) permite que `runs` dependa de `files`, mas não o inverso.
