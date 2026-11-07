-- Ausência: nenhum evento amplo no stream de traces no período (docs/GoblinShiftPlan.md, 3.7). A
-- manutenção emite um span a cada 30 s mesmo sem tráfego, então o stream vazio aponta para a
-- exportação (aplicação, Collector ou ingestão). Complementa o heartbeat, que lê métricas e prova
-- que os alertas são avaliados. `main = 'true'` deixa de fora a semente do provisionamento.
SELECT count(*) AS spans
FROM "${stream}"
WHERE main = 'true'
HAVING count(*) = 0
