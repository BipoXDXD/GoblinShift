# ADR 007. `StoredObject` e exclusão sempre adiada

- **Estado:** aceito
- **Plano:** [1.7](../GoblinShiftPlan.md#s1-7), [3.3](../GoblinShiftPlan.md#s3-3), [3.4](../GoblinShiftPlan.md#s3-4), [3.6](../GoblinShiftPlan.md#s3-6), [3.10](../GoblinShiftPlan.md#s3-10)
- **Referências:** [B03](../GoblinShiftReferencias.md#l-1)

## Contexto

Um mesmo arquivo físico pode ser referenciado por mais de um registro: no Downloads Detox, o artefato aponta para o mesmo objeto da entrada, e os downloads individuais reutilizam os bytes privados. Excluir um upload ou uma saída não pode apagar bytes que uma execução ou um download em andamento ainda usa.

## Decisão

`StoredObject` é a supertabela comum a `Upload` e `Artifact`: cada um guarda um `object_id` com FK real para ele. O objeto tem proprietário, `storage_key` único, SHA-256, bytes (`bigint`) e estado (`pending`, `committed`, `delete_requested`, `deleted`, `missing`). A exclusão é sempre adiada: excluir um upload ou uma saída torna o recurso indisponível na hora e marca o objeto como `delete_requested`. O processo `maintenance` remove o arquivo só quando nenhum `Upload` ou `Artifact` válido aponta para ele e não há `ObjectUse` aberto (execução ou download em andamento).

## Alternativas descartadas

| Alternativa                          | Motivo                                                                                                                                               |
|--------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| Associação polimórfica em `Artifact` | O antigo "objeto ou referência física" exigiria uma associação polimórfica, sem FK possível                                                          |
| `409` ao excluir arquivo em uso      | Excluir nunca deve responder "arquivo em uso"; adiar a exclusão "defines errors out of existence" ([B03](../GoblinShiftReferencias.md#l-1), cap. 10) |

## Consequências

- O reaproveitamento do Detox é seguro: o mesmo objeto, duas referências, e renomear não duplica objetos.
- Exclusão de saída não apaga o original; objetos ausentes inesperadamente viram `missing`, nunca arquivos vazios.
- Um stream já aberto pode terminar dentro do timeout; a remoção física aguarda processamento e streams ativos.
- Objetos pendentes de exclusão contam no armazenamento (4 GiB por conta e 20 GiB no ambiente) até serem removidos.
- A manutenção tem um limitador: no máximo 500 objetos ou 2 GiB removidos por ciclo; acima disso, para e alerta o administrador.
- `files` não conhece execuções: `runs` registra o uso com `hold` na aceitação e o libera com `release` no estado terminal.

## Como verificar

- A migração que cria `StoredObject`, `Upload` e `Artifact` (fatia 3) tem `object_id` como FK, com `UNIQUE (owner_id, id)` no objeto e FK composta `(owner_id, object_id)` `NOT NULL` nos dois; nenhuma coluna de associação polimórfica.
- Os testes de exclusão de arquivo em uso ([3.7](../GoblinShiftPlan.md#s3-7)) passam a partir da fatia 4, quando entra o processo `maintenance`, que faz a remoção física: `DELETE /uploads/{id}` com execução ativa responde `202`, o upload fica indisponível na hora e o arquivo físico continua até o `release` do uso.
- Na fatia 5, a suíte do Detox confirma que o `Artifact` de saída aponta para o mesmo `StoredObject` da entrada, sem cópia física.
