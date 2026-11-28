-- Causa: arquivo que não sai do disco depois de a exclusão ser confirmada no banco
-- (docs/GoblinShiftPlan.md, 1.7 e 3.4). A chave entra na outbox `file_removal` na transação que
-- tira o arquivo de uso, e a manutenção repete a remoção a cada volta até ele sair. Uma falha
-- isolada se resolve sozinha e não dispara: são pelo menos três voltas com remoção pendente
-- (`maintenance.file_removals_pending`) no período. O evento `alert.purge_file_not_removed`
-- (`AlertEventCounter`, em qualquer span `main`) vem só da remoção sem outbox, a das chaves que o
-- worker reservou e não chegou a registrar no banco: nada a repete, e um evento basta, por isso
-- vale três voltas. A chave de cada arquivo fica no log do processo.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND (
    TRY_CAST(alert_purge_file_not_removed AS BIGINT) > 0
    OR (
      operation_name = 'maintenance.cycle'
      AND TRY_CAST(maintenance_file_removals_pending AS BIGINT) > 0
    )
  )
HAVING sum(CASE WHEN TRY_CAST(alert_purge_file_not_removed AS BIGINT) > 0 THEN 3 ELSE 1 END) >= 3
