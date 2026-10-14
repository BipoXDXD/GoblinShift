-- Queima do orçamento de erro em duas janelas (docs/GoblinShiftPlan.md, 3.7): 1 h e os 15 min
-- mais recentes. Devolve uma linha só quando as duas janelas queimam no ritmo pedido e têm o
-- mínimo de eventos elegíveis; o alerta dispara com uma linha ou mais.
--
-- Com meta 1 - 1/N, queimar a X vezes o ritmo permitido é ter ruins/elegíveis >= X/N, isto é,
-- ruins * N >= X * elegíveis, em inteiros, sem arredondamento.
-- _timestamp está em microssegundos.
SELECT eligible_long, bad_long, eligible_short, bad_short
FROM (
  SELECT
    count(*) AS eligible_long,
    COALESCE(sum(1 - good), 0) AS bad_long,
    COALESCE(sum(CASE WHEN _timestamp >= (to_unixtime(now()) - 900) * 1000000 THEN 1 ELSE 0 END), 0)
      AS eligible_short,
    COALESCE(
      sum(CASE WHEN _timestamp >= (to_unixtime(now()) - 900) * 1000000 THEN 1 - good ELSE 0 END), 0
    ) AS bad_short
  FROM (${events}) AS events
  WHERE _timestamp >= (to_unixtime(now()) - 3600) * 1000000
) AS windows
WHERE eligible_long >= ${min_events_long}
  AND eligible_short >= ${min_events_short}
  AND bad_long * ${budget_denominator} >= ${burn_rate} * eligible_long
  AND bad_short * ${budget_denominator} >= ${burn_rate} * eligible_short
