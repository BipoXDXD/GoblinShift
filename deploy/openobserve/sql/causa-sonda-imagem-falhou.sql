-- Causa: a sonda da imagem do executor não obtém resposta (`executor-ctl` ou Docker parados, rota
-- antiga ou imagem fora da allowlist). Pega também o host parado sem tráfego. São voltas de 30 s:
-- dez ou mais no período equivalem a uns 5 min de falha, e um restart do serviço não chega lá.
SELECT count(*) AS cycles
FROM "${stream}"
WHERE main = 'true'
  AND operation_name = 'maintenance.cycle'
  AND TRY_CAST(maintenance_executor_image_probe_failed AS BIGINT) > 0
HAVING count(*) >= 10
