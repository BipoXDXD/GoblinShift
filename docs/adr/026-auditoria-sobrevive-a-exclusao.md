# ADR 026. Auditoria sobrevive à exclusão da conta

- **Estado:** aceito na Fase 0 da fatia 6 (2026-10-14), decisão D-7 do plano da fatia
- **Plano:** [1.8](../GoblinShiftPlan.md#s1-8), [3.3](../GoblinShiftPlan.md#s3-3), [3.6](../GoblinShiftPlan.md#s3-6)

## Contexto

`DELETE /me` remove a conta e todos os seus dados em até 24 horas (1.8). Os eventos de segurança (login, CSRF recusado, emissão de chave, bloqueio) servem à defesa do próprio serviço e ficam 90 dias com IP truncado, como o aviso de privacidade declara.

## Decisão

- `audit_event.user_id` **não tem FK** para `app_user`. A purga apaga a conta e os eventos ficam até completar 90 dias, quando a manutenção os remove em lotes.
- O evento guarda só tipo, resultado, momento, `user_id` e o IP truncado (/24 em IPv4, /48 em IPv6, imposto por CHECK no banco). Nada de nome de arquivo, conteúdo, token ou nome de exibição.
- Só são auditados eventos de quem tem credencial válida, além do login, já limitado por IP no Nginx; um atacante sem credencial cai no 401 e não inunda a tabela.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Apagar os eventos junto com a conta | Perde o rastro de abuso justamente de quem apaga a conta para sumir |
| Trocar `user_id` por HMAC | Mais uma chave para gerir; depois da purga o UUID já não identifica ninguém sem `app_user` |

## Consequências

- O aviso de privacidade precisa citar a retenção de 90 dias dos eventos de segurança após a exclusão.
- Consultas da auditoria não fazem JOIN obrigatório com `app_user`.

## Como verificar

- `tests/test_schema_fatia_6.py`: evento de conta inexistente é aceito; apagar a conta mantém os eventos; IP fora de /24 ou /48 é recusado.
- `tests/identity/test_audit.py`: `truncate_ip` (IPv4, IPv6, IPv4 mapeado, inválido) e `record`.
- Frente C: `test_auditoria_sobrevive_a_purga_sem_identificar`.
