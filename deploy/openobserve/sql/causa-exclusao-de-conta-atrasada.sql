-- Causa: exclusão de conta atrasada (docs/GoblinShiftPlan.md, 1.8 e 3.7). O `DELETE /me` promete
-- a remoção em até 24 h; o ciclo de contas da manutenção conta as contas com pedido de 20 h ou
-- mais (`identity.purge.DELETION_OVERDUE_AFTER`), o que deixa 4 h para agir.
SELECT max(TRY_CAST(maintenance_accounts_deletion_overdue AS BIGINT)) AS accounts
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
HAVING max(TRY_CAST(maintenance_accounts_deletion_overdue AS BIGINT)) > 0
