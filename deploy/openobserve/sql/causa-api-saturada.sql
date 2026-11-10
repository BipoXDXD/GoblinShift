-- Causa: API saturada, recusando pedidos no teto de concorrência do uvicorn (docs/GoblinShiftPlan.md,
-- 3.7). Acima de `--limit-concurrency`, o uvicorn responde 503 antes da aplicação: o pedido não
-- abre span nem conta em SLO nenhum. A API conta as recusas e exporta, a cada minuto com alguma,
-- um span `api.saturation` com a contagem em `api.saturation.rejected`. Um minuto isolado é pico
-- absorvido; o alerta dispara com recusas em pelo menos três fatias de 1 min dos últimos 10 min.
-- Com duas réplicas da API na troca sem queda, cada uma exporta o próprio span, e a fatia conta
-- uma vez só.
SELECT
  sum(TRY_CAST(api_saturation_rejected AS BIGINT)) AS rejected,
  count(DISTINCT TRY_CAST((to_unixtime(now()) * 1000000 - _timestamp) / 60000000 AS BIGINT)) AS minutes
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'api.saturation'
  AND TRY_CAST(api_saturation_rejected AS BIGINT) > 0
  AND _timestamp >= (to_unixtime(now()) - 600) * 1000000
HAVING count(DISTINCT TRY_CAST((to_unixtime(now()) * 1000000 - _timestamp) / 60000000 AS BIGINT)) >= 3
