-- Causa: filesystem do PostgreSQL, do storage ou da telemetria acima de 85%
-- (docs/GoblinShiftPlan.md, 3.7 e 3.8). A métrica vem do receiver hostmetrics do Collector,
-- que só lê os pontos de montagem listados em docker/otel-collector/config.yaml.
-- O menor valor no período do alerta: dispara só com o disco acima de 85% o tempo todo, não
-- num pico de um minuto.
SELECT mountpoint, min(value) AS utilization
FROM "system_filesystem_utilization"
GROUP BY mountpoint
HAVING min(value) > 0.85
