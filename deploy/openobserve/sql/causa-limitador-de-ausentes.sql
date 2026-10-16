-- Causa: o limitador da varredura de objetos ausentes acionado (docs/GoblinShiftPlan.md, 1.7 e
-- 3.8). Mais da metade de um lote (ou um lote pequeno inteiro) sem arquivo no disco: storage errado
-- ou backup antigo restaurado. Nada foi marcado e o lote volta a cada volta até o administrador
-- decidir. O span `maintenance.cycle` traz `maintenance.missing_governor_tripped = 1`.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_missing_governor_tripped AS BIGINT) = 1
HAVING count(*) > 0
