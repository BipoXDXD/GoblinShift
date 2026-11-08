-- Causa: o livro-razão de capacidade fora do lugar (ADR 024; capacity/reconciliation.py). A
-- reconciliação corrige o pool divergente, mas cada correção indica reserva que alguém deixou de
-- gravar ou de liberar; acima do teto, o pool fica no teto. O contador `alert.<evento>` vem do
-- `AlertEventCounter` e os detalhes ficam no log da manutenção. Uma volta assim no período basta.
SELECT count(*) AS spans
FROM "${stream}"
WHERE main = 'true'
  AND (
    TRY_CAST(alert_capacity_divergence AS BIGINT) > 0
    OR TRY_CAST(alert_capacity_over_max AS BIGINT) > 0
  )
HAVING count(*) > 0
