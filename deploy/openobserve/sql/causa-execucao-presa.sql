-- Causa: execução presa em `stopping` há mais de 10 minutos (docs/GoblinShiftPlan.md, 3.7).
-- A manutenção conta, a cada volta, as Runs em `stopping` desde antes de 10 min (o primeiro
-- entre o pedido de cancelamento e o prazo vencido) e grava a contagem no span
-- `maintenance.cycle`. Uma volta com contagem acima de zero no período basta: a idade já está
-- na contagem. Presa assim, a Run segura a única vaga de tentativa física e a reserva dela.
SELECT max(TRY_CAST(maintenance_runs_stuck_stopping AS BIGINT)) AS runs
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
HAVING max(TRY_CAST(maintenance_runs_stuck_stopping AS BIGINT)) > 0
