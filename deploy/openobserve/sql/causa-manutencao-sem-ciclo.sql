-- Ausência: nenhuma volta da manutenção concluída no período (docs/GoblinShiftPlan.md, 3.7). O span
-- `maintenance.cycle` é exportado no fim da volta, então cobre o processo parado e a volta travada.
-- Ao contrário das causas, dispara com a contagem em zero: como no heartbeat, o agregado sem
-- GROUP BY devolve uma linha mesmo num período sem dados. O período passa da suspensão da
-- manutenção pelas sondas da aceitação (ADR 017).
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
HAVING count(*) = 0
