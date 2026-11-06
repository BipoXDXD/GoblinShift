-- Causa: arquivo que ficou no disco depois de a exclusão ser confirmada no banco
-- (docs/GoblinShiftPlan.md, 1.7 e 3.4). O banco muda antes de o arquivo sair: o objeto do expurgo
-- já está `deleted` e o pendente já perdeu a linha, então nada repete a remoção e o arquivo passa
-- da retenção prometida. Uma volta assim no período basta. O span `maintenance.cycle` conta por
-- origem; a chave de cada arquivo fica no log da manutenção.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND (
    TRY_CAST(maintenance_purge_files_not_removed AS BIGINT) > 0
    OR TRY_CAST(maintenance_candidate_files_not_removed AS BIGINT) > 0
  )
HAVING count(*) > 0
