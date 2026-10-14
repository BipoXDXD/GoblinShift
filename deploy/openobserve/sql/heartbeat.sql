-- Heartbeat ("dead man's switch"): sempre devolve uma linha, então o alerta dispara a cada
-- avaliação e pinga o healthchecks.io. Se o OpenObserve parar de avaliar alertas, o ping some
-- e o healthchecks.io avisa por um canal fora da VPS.
-- Lê as métricas de disco, que o Collector manda a cada minuto mesmo sem nenhuma requisição:
-- o stream de traces fica vazio numa hora sem tráfego, e o período nunca fica sem dados aqui.
SELECT count(*) AS samples
FROM "system_filesystem_utilization"
HAVING count(*) >= 0
