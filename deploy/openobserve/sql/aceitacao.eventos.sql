-- SLO de aceitação (docs/GoblinShiftPlan.md, 3.7): um evento por POST /runs, meta 99,5%.
-- Elegível: credencial e corpo válidos, isto é, 202, ou falha do servidor (5xx). Ficam fora as
-- recusas do cliente e as de política (4xx: cota, taxa, execução ativa) e a fila cheia (503
-- QUEUE_FULL), que é o serviço se protegendo. A validação (?validate_only=true) responde 200 e
-- também fica fora, porque não cria Run.
-- Bom: 202 em menos de 500 ms.
-- Os atributos dos spans chegam ao OpenObserve como texto, com "_" no lugar de "."; o TRY_CAST
-- converte e devolve NULL (evento ruim ou fora) para um valor inesperado, sem derrubar a consulta.
SELECT
  _timestamp,
  CASE
    WHEN TRY_CAST(http_status_code AS BIGINT) = 202 AND TRY_CAST(duration_ms AS DOUBLE) < 500 THEN 1
    ELSE 0
  END AS good
FROM "${stream}"
WHERE main = 'true'
  AND http_method = 'POST'
  AND http_route = '/api/v1/runs'
  AND (
    TRY_CAST(http_status_code AS BIGINT) = 202
    OR (TRY_CAST(http_status_code AS BIGINT) >= 500 AND COALESCE(error_code, '') <> 'QUEUE_FULL')
  )
