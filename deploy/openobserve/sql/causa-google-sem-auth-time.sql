-- Causa: login no Google sem o claim `auth_time` (ADR 062). Acontece com "Session age claims"
-- desligado no console do Google ou com o app não verificado. O login segue (falha aberta), mas a
-- recência da sessão cai na hora do callback, e um navegador logado no Google há dias volta a
-- abrir `DELETE /me` e a emissão de chave sem senha. O contador `alert.google_auth_time_missing`
-- vem do `AlertEventCounter` no evento amplo da requisição do callback. Um evento no período basta.
SELECT count(*) AS spans
FROM "${stream}"
WHERE main = 'true'
  AND TRY_CAST(alert_google_auth_time_missing AS BIGINT) > 0
HAVING count(*) > 0
