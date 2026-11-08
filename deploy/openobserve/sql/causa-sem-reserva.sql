-- Causa: upload concluído ou saída publicada sem reserva de capacidade (ADR 024). A operação segue
-- e a reconciliação adota o objeto, mas só registros anteriores à 0006 deveriam cair aqui. Os
-- eventos vêm da API e do worker, em spans diferentes, por isso a consulta não filtra a operação. O
-- contador `alert.<evento>` vem do `AlertEventCounter`; o ID fica no log. Um evento no período basta.
SELECT count(*) AS spans
FROM "${stream}"
WHERE main = 'true'
  AND (
    TRY_CAST(alert_upload_without_reservation AS BIGINT) > 0
    OR TRY_CAST(alert_run_without_reservation AS BIGINT) > 0
  )
HAVING count(*) > 0
