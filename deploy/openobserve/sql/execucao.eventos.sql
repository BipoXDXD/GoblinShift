-- SLO de execução (docs/GoblinShiftPlan.md, 3.7): um evento por Run terminada, meta 99%.
-- O evento é o span "run.finished" com main = true que o worker emite no término da Run
-- (contrato em deploy/openobserve/README.md). Ele já diz se a Run é elegível: até 10 arquivos e
-- 50 MiB de entrada, e não cancelada pela pessoa usuária.
-- Bom: sem falha de infraestrutura nem de parada e em até 5 min desde o início da primeira
-- tentativa. Rejeições de conteúdo (NO_VALID_OUTPUTS, limites da receita) não gastam orçamento.
-- Uma Run que nunca começou não tem duração e conta como ruim; ela só termina assim por
-- QUEUE_EXPIRED ou DEADLINE_EXCEEDED, que já são falhas.
-- Os atributos dos spans chegam ao OpenObserve como texto, com "_" no lugar de "."; o TRY_CAST
-- converte e devolve NULL (evento ruim ou fora) para um valor inesperado, sem derrubar a consulta.
SELECT
  _timestamp,
  CASE
    WHEN COALESCE(error_code, '') NOT IN (
        'EXECUTOR_UNAVAILABLE', 'INFRA_POISON', 'DEADLINE_EXCEEDED', 'QUEUE_EXPIRED'
      )
      AND TRY_CAST(run_duration_ms AS DOUBLE) <= 300000
      THEN 1
    ELSE 0
  END AS good
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'run.finished'
  AND slo_execution_eligible = 'true'
