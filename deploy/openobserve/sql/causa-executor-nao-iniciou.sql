-- Causa: o executor não inicia (docs/GoblinShiftPlan.md, 3.7). Cada start recusado pelo executor-ctl
-- soma `alert.executor_start_failed` no span `attempt` (imagem fora do daemon, Docker parado ou ctl
-- fora do ar). A Run repete até 4 claims em cerca de 2 min e termina em INFRA_POISON; a queima do
-- SLO exige 51 Runs na hora e não vê isso com pouco tráfego. Um start isolado que se recupera na
-- retentativa não dispara: são pelo menos três no período.
SELECT count(*) AS attempts
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'attempt'
  AND TRY_CAST(alert_executor_start_failed AS BIGINT) > 0
HAVING count(*) >= 3
