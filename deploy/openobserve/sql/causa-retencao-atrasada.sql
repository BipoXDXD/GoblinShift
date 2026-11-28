-- Causa: retenção atrasando sem exceção (#442, 21b). A manutenção acompanha, em memória, os
-- acúmulos da retenção (`runs_to_delete`, `uploads_to_delete`, `idempotency_records_expired` e
-- `request_windows`) e grava no span `maintenance.cycle` as voltas seguidas em que algum deles
-- cresceu. Três ou mais é lote pequeno demais para a entrada ou linha presa por trava: o ciclo
-- não falha, então nenhuma outra causa vê. Reiniciar a manutenção zera a sequência.
SELECT max(TRY_CAST(maintenance_retention_backlog_growing AS BIGINT)) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
HAVING max(TRY_CAST(maintenance_retention_backlog_growing AS BIGINT)) >= 3
