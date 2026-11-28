-- Causa: retenção da auditoria atrasada (docs/GoblinShiftPlan.md, 1.7 e 3.7; #442, 50b). Os
-- eventos de auditoria saem com 90 dias; o ciclo de contas da manutenção apaga em lotes até o
-- prazo de cada volta e conta os eventos com mais de 91 dias
-- (`identity.purge.AUDIT_OVERDUE_AFTER`): um dia de atraso é entrada maior que o laço.
SELECT max(TRY_CAST(maintenance_audit_events_overdue AS BIGINT)) AS events
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
HAVING max(TRY_CAST(maintenance_audit_events_overdue AS BIGINT)) > 0
