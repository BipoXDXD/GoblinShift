-- Causa: ciclo da manutenção falhando (docs/GoblinShiftPlan.md, 3.4 e 3.7). `run_cycles` conta em
-- `maintenance.failed_cycles` os ciclos que levantaram exceção, cujas contagens ficam fora do span
-- daquela volta, e os que tiveram passo falho (`maintenance.steps_failed`). A volta seguinte tenta
-- de novo, e por isso uma falha isolada não dispara: são pelo menos três voltas no período.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_failed_cycles AS BIGINT) > 0
HAVING count(*) >= 3
