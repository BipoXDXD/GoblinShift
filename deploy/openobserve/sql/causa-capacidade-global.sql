-- Causa: armazenamento reservado no ambiente acima de 85% do teto global (1.7: 20 GiB),
-- docs/GoblinShiftPlan.md 1.8 e 3.7. A manutenção lê o pool global do livro-razão (ADR 024) a
-- cada volta. O menor valor no período, como no alerta de disco: dispara só com o ambiente
-- acima de 85% o tempo todo, não num pico entre dois expurgos. Eventos sem o teto ficam fora.
SELECT
  min(
    TRY_CAST(maintenance_storage_global_reserved_bytes AS DOUBLE)
    / NULLIF(TRY_CAST(maintenance_storage_global_max_bytes AS DOUBLE), 0)
  ) AS utilization
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_storage_global_max_bytes AS DOUBLE) > 0
HAVING min(
    TRY_CAST(maintenance_storage_global_reserved_bytes AS DOUBLE)
    / NULLIF(TRY_CAST(maintenance_storage_global_max_bytes AS DOUBLE), 0)
  ) > 0.85
