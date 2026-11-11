-- Causa: a imagem do executor configurada na manutenção (`GS_EXECUTOR_IMAGE`) saiu do daemon. O
-- `executor-ctl` usa `--pull=never`, então a próxima Run termina em `docker_unavailable` sem o
-- alerta do start ter como avisar antes. O span `maintenance.cycle` traz
-- `maintenance.executor_image_missing = 1`; uma volta assim no período basta, porque "ausente" é
-- resposta do daemon, nunca falha da sonda.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_executor_image_missing AS BIGINT) = 1
HAVING count(*) > 0
