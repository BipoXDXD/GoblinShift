-- Causa: a limpeza dos temporários `.part` falhando (docs/GoblinShiftPlan.md, 1.7). A queda no meio
-- de um upload deixa o `.part` no storage, fora do livro-razão; a limpeza roda isolada do resto do
-- ciclo, então a falha não entra em `maintenance.failed_cycles`. Vale tanto para o diretório
-- ilegível quanto para um arquivo que não saiu. O lote volta na volta seguinte, e por isso uma
-- falha isolada não dispara: são pelo menos três voltas no período. `tmp_parts_bytes` não tem
-- limiar, porque inclui o upload em curso. O traceback fica no log da manutenção.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_tmp_parts_sweep_failed AS BIGINT) = 1
HAVING count(*) >= 3
