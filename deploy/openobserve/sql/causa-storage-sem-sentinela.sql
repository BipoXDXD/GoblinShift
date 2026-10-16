-- Causa: a varredura de objetos ausentes suspensa por falta da sentinela de montagem
-- (docs/GoblinShiftPlan.md, 1.7 e 3.8; files/storage.py, MOUNT_MARKER). Quase sempre o filesystem
-- do storage desmontado: sem a sentinela nada é marcado `missing`, mas a aplicação grava no disco
-- do sistema. O span `maintenance.cycle` traz `maintenance.missing_sweep_skipped = 1`; uma volta
-- assim no período basta.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_missing_sweep_skipped AS BIGINT) = 1
HAVING count(*) > 0
