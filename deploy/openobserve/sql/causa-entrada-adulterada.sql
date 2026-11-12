-- Causa: o storage devolveu uma entrada adulterada (symlink no lugar do objeto), ADR 054. É
-- incidente de integridade, não de infraestrutura: a Run termina INPUT_TAMPERED na primeira
-- tentativa, sem retry, e fica fora do SLO de execução, então só este alerta torna o fato visível
-- fora da VPS. O contador `alert.input_tampered` vem do `AlertEventCounter` no span `attempt`; o
-- log do worker traz `run_id` e a posição da entrada, nunca o caminho. A varredura de ausentes da
-- manutenção emite o mesmo evento para symlink ou diretório no lugar de um objeto, e o contador cai
-- no span `maintenance.cycle` (também `main = 'true'`). Um evento no período basta.
SELECT count(*) AS attempts
FROM "${stream}"
WHERE main = 'true'
  AND TRY_CAST(alert_input_tampered AS BIGINT) > 0
HAVING count(*) > 0
