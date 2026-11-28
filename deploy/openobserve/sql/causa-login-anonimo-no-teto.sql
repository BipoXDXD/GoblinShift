-- Causa: o login sem autenticação bateu num teto da aplicação (plano login-e-borda, D6). O teto
-- global de estados vivos (`MAX_LIVE_LOGIN_STATES`) recusou inícios novos: enquanto durar, ninguém
-- começa um login. Ou o teto de `LOGIN_FAILED` sem conta por minuto deixou recusas só no log, sem
-- evento para o código de referência achar. O contador `alert.<evento>` vem do `AlertEventCounter`
-- no evento amplo da requisição; a API avisa no máximo uma vez por minuto por processo e por teto.
-- Um evento no período basta.
SELECT count(*) AS spans
FROM "${stream}"
WHERE main = 'true'
  AND (
    TRY_CAST(alert_login_state_cap AS BIGINT) > 0
    OR TRY_CAST(alert_login_failure_audit_cap AS BIGINT) > 0
  )
HAVING count(*) > 0
