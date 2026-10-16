-- Causa: o limitador de exclusões da manutenção acionado (docs/GoblinShiftPlan.md, 3.4 e 3.7).
-- Apagar é a direção perigosa: cada volta remove no máximo 500 objetos ou 2 GiB. Quando havia
-- mais do que isso, o span `maintenance.cycle` traz `maintenance.purge_limit_reached = 1`. Uma
-- volta assim no período basta; o administrador confere se a exclusão em massa é legítima.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_purge_limit_reached AS BIGINT) = 1
HAVING count(*) > 0
