-- Causa: a varredura de objetos ausentes falhando (docs/GoblinShiftPlan.md, 1.7 e 3.8). A falha fica
-- isolada e o resto do ciclo segue, então ela não entra em `maintenance.failed_cycles`. Cobre também
-- a sentinela ilegível. O lote volta na volta seguinte, e por isso uma falha isolada não dispara:
-- são pelo menos três voltas no período. O traceback fica no log da manutenção.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_missing_sweep_failed AS BIGINT) = 1
HAVING count(*) >= 3
