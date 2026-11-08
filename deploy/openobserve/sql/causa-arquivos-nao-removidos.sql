-- Causa: arquivo que ficou no disco depois de a exclusão ser confirmada no banco
-- (docs/GoblinShiftPlan.md, 1.7 e 3.4). O banco muda antes de o arquivo sair: o objeto do expurgo
-- já está `deleted` e o pendente já perdeu a linha, então nada repete a remoção e o arquivo passa
-- da retenção prometida. Um evento assim no período basta. O span `maintenance.cycle` conta por
-- origem. A remoção também acontece no cancelamento pela API e no fim da tentativa, e pode perder
-- a contagem num ciclo que falha: `alert.purge_file_not_removed` (`AlertEventCounter`) cobre esses
-- casos em qualquer span `main`. A chave de cada arquivo fica no log do processo.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND (
    TRY_CAST(alert_purge_file_not_removed AS BIGINT) > 0
    OR (
      operation_name = 'maintenance.cycle'
      AND (
        TRY_CAST(maintenance_purge_files_not_removed AS BIGINT) > 0
        OR TRY_CAST(maintenance_candidates_left_on_disk AS BIGINT) > 0
      )
    )
  )
HAVING count(*) > 0
