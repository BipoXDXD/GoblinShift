# ADR 020. Downloads Detox: a receita copia os bytes, o worker reaproveita o objeto da entrada

- **Estado:** aceito na etapa 0 da fatia 5 (2026-10-14), pendente de revisão do usuário no PR da fatia; implementado na integração
- **Plano:** [1.4](../GoblinShiftPlan.md#s1-4), [3.2](../GoblinShiftPlan.md#s3-2), [3.3](../GoblinShiftPlan.md#s3-3), [3.6](../GoblinShiftPlan.md#s3-6)
- **Relacionado:** [ADR 007](007-storedobject-e-exclusao-adiada.md), [ADR 019](019-layout-de-out-pacote-e-papeis.md)

## Contexto

O Detox não transforma conteúdo: renomeia, agrupa e deduplica. O plano pede que renomear não duplique objetos no storage (1.4). `publish_artifact` já aceita apontar um `Artifact` para um objeto existente, mas o executor só enxerga `out/` e o worker sempre gravava uma cópia de cada saída.

## Decisão

- **A receita copia os bytes da entrada para `out/`**, como qualquer outra receita: o contrato do executor, da coleta e do empacotador não muda, e o ZIP lê só `out/`.
- **O worker decide o reaproveitamento** ao guardar os candidatos: se o SHA-256 e o tamanho de uma saída, calculados pelo próprio worker, forem iguais aos de uma entrada da Run cujo objeto ainda está `committed`, o `Artifact` aponta para o objeto da entrada e nada é gravado no storage. Se o objeto da entrada já estiver `delete_requested`, grava a cópia normalmente.
- Vale para qualquer receita cuja saída seja idêntica a uma entrada, não só para o Detox.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| `OutputDir.reference(posição, nome)` e campo novo no relatório | Sem cópia no executor, mas o empacotador e a coleta teriam de ler bytes fora de `out/`, e o worker passaria a confiar numa declaração do executor |
| Sempre copiar para o storage | Viola 1.4 ("renomear não duplica objetos") |

## Consequências

- Cópia temporária na área do executor: até 480 MiB dos 6 GiB ([ADR 005](005-area-do-executor-em-filesystem-fixo.md)).
- O worker não confia no executor: o hash comparado é o que ele mesmo calcula na coleta.
- `HeldContent` passa a expor o `object_id` da entrada (integração).

## Como verificar

- Teste de integração (PostgreSQL real): a publicação de uma Run do Detox cria o `Artifact` sobre o mesmo `StoredObject` da entrada, sem candidato novo; com o upload excluído antes da publicação, grava a cópia.
- E2E na VPS: o `Artifact` de uma imagem tem o mesmo SHA-256 do upload.
