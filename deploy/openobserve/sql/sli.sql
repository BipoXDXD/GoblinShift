-- SLI de um SLO: elegíveis e bons no intervalo da consulta (30 dias no relatório).
-- ${events} é a consulta de eventos do SLO (<slo>.eventos.sql), com uma linha por evento
-- elegível e a coluna good (1 bom, 0 ruim).
SELECT count(*) AS eligible, COALESCE(sum(good), 0) AS good
FROM (${events}) AS events
