-- Causa: API saturada, recusando pedidos no teto de concorrência do uvicorn (docs/GoblinShiftPlan.md,
-- 3.7). Acima de `--limit-concurrency`, o uvicorn responde 503 antes da aplicação: o pedido não
-- abre span nem conta em SLO nenhum. A API conta as recusas e, 1 s depois de cada virada do minuto
-- de relógio em que houve alguma, exporta um span `api.saturation` com a contagem em
-- `api.saturation.rejected`; na parada, exporta o minuto incompleto. Um pico curto é absorvido: o
-- alerta dispara com spans em pelo menos três minutos absolutos (`_timestamp` / 60 s) dos últimos
-- 10 min. Na troca sem queda, as duas réplicas relatam na mesma virada e o relato da parada cai no
-- minuto do último relato regular, então os spans do mesmo minuto contam uma vez só.
SELECT
  sum(TRY_CAST(api_saturation_rejected AS BIGINT)) AS rejected,
  count(DISTINCT TRY_CAST(_timestamp / 60000000 AS BIGINT)) AS minutes
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'api.saturation'
  AND TRY_CAST(api_saturation_rejected AS BIGINT) > 0
  AND _timestamp >= (to_unixtime(now()) - 600) * 1000000
HAVING count(DISTINCT TRY_CAST(_timestamp / 60000000 AS BIGINT)) >= 3
