-- Causa: fila global cheia por mais de 15 minutos (docs/GoblinShiftPlan.md, 3.7).
-- A fila cheia aparece como recusa QUEUE_FULL no POST /runs. O alerta dispara quando houve
-- recusa em cada uma das três fatias de 5 min dos últimos 15 min. Sem ninguém tentando criar
-- execuções, a fila cheia não é vista, e também não recusa ninguém.
SELECT
  count(*) AS refusals,
  count(DISTINCT TRY_CAST((to_unixtime(now()) * 1000000 - _timestamp) / 300000000 AS BIGINT)) AS slices
FROM "${stream}"
WHERE main = 'true'
  AND http_method = 'POST'
  AND http_route = '/api/v1/runs'
  AND error_code = 'QUEUE_FULL'
  AND _timestamp >= (to_unixtime(now()) - 900) * 1000000
HAVING count(DISTINCT TRY_CAST((to_unixtime(now()) * 1000000 - _timestamp) / 300000000 AS BIGINT)) >= 3
