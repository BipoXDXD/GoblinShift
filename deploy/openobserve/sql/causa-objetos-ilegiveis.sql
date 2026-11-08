-- Causa: objetos que a varredura não conseguiu conferir (docs/GoblinShiftPlan.md, 1.7 e 3.8). O
-- `stat` do objeto falhou com erro que não é ausência (EIO, permissão), nada foi marcado e, sem
-- ausentes no lote, o limitador não aciona. A sentinela ilegível fica com
-- `causa-varredura-falhou`, porque derruba a varredura antes de conferir objetos. O storage não tem
-- backup, então uma volta assim no período basta. O span `maintenance.cycle` traz
-- `maintenance.objects_missing_unreadable`; o errno e as chaves ficam no log da manutenção.
SELECT sum(TRY_CAST(maintenance_objects_missing_unreadable AS BIGINT)) AS objects
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_objects_missing_unreadable AS BIGINT) > 0
HAVING count(*) > 0
