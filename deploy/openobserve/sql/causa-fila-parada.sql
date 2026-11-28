-- Causa: fila parada com o worker fora (#442, 21a). A manutenção conta, a cada volta, as Runs
-- elegíveis há mais de 10 min (`queued`, ou `running` em `retry_wait`) quando nenhuma tentativa
-- aberta tem o lease em dia, e grava a contagem no span `maintenance.cycle`. Com pouco tráfego o
-- SLO de execução não avalia e `causa-sem-traces` não dispara (API e manutenção emitem); sem este
-- alerta, as Runs venceriam em `QUEUE_EXPIRED` sem aviso. Uma volta acima de zero no período basta.
SELECT max(TRY_CAST(maintenance_queue_stalled_runs AS BIGINT)) AS runs
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
HAVING max(TRY_CAST(maintenance_queue_stalled_runs AS BIGINT)) > 0
