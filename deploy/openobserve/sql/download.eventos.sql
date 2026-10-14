-- SLO de download (docs/GoblinShiftPlan.md, 3.7): um evento por GET de download, meta 99,5%.
-- Elegível: artefato disponível e autorizado (2xx) ou falha do servidor (5xx). Os 4xx
-- (inexistente, alheio, expirado, limite de transferências) ficam fora.
-- Bom: 2xx, primeiro byte em menos de 1 s e corpo enviado até o fim. Uma transferência
-- interrompida conta como ruim, mesmo quando foi o cliente que desistiu: o evento não distingue
-- os dois casos, e o mínimo de eventos da queima absorve os casos isolados.
-- Os atributos dos spans chegam ao OpenObserve como texto, com "_" no lugar de "."; o TRY_CAST
-- converte e devolve NULL (evento ruim ou fora) para um valor inesperado, sem derrubar a consulta.
SELECT
  _timestamp,
  CASE
    WHEN TRY_CAST(http_status_code AS BIGINT) < 300
      AND TRY_CAST(http_response_first_byte_ms AS DOUBLE) < 1000
      AND http_response_completed = 'true'
      THEN 1
    ELSE 0
  END AS good
FROM "${stream}"
WHERE main = 'true'
  AND http_method = 'GET'
  AND http_route = '/api/v1/artifacts/{artifact_id}/download'
  AND (
    (TRY_CAST(http_status_code AS BIGINT) >= 200 AND TRY_CAST(http_status_code AS BIGINT) < 300)
    OR TRY_CAST(http_status_code AS BIGINT) >= 500
  )
