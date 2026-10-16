-- Causa: objetos marcados `missing` pela varredura (docs/GoblinShiftPlan.md, 1.7 e 3.8). O
-- arquivo sumiu do storage com a montagem conferida e abaixo do limitador; `missing` é final e a
-- reserva já foi liberada. Esperado depois de restaurar o banco de um backup; fora disso, o
-- administrador investiga. O span `maintenance.cycle` traz `maintenance.objects_marked_missing`.
SELECT sum(TRY_CAST(maintenance_objects_marked_missing AS BIGINT)) AS objects
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_objects_marked_missing AS BIGINT) > 0
HAVING count(*) > 0
