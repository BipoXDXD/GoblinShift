-- Causa: arquivo que ficou no disco depois de a linha sair do banco (docs/GoblinShiftPlan.md, 1.7
-- e 3.4). A linha é confirmada antes de o arquivo sair, e a chave não volta: nada repete a remoção
-- e o arquivo passa da retenção prometida. Uma volta assim no período basta. O span
-- `maintenance.cycle` conta por origem; a chave de cada arquivo fica no log da manutenção.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND (
    TRY_CAST(maintenance_purge_files_not_removed AS BIGINT) > 0
    OR TRY_CAST(maintenance_candidate_files_not_removed AS BIGINT) > 0
  )
HAVING count(*) > 0
