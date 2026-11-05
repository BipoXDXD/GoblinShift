# Monitor de uptime externo

Worker `goblinshift-uptime` da Cloudflare. A cada 2 min consulta `https://goblinshift.dev/readyz` e avisa direto no Discord, sem passar por nada na VPS: se ela cair, a telemetria cai junto, então este aviso precisa vir de fora ([3.7](../../docs/GoblinShiftPlan.md#s3-7)).

- **Fora do ar:** duas falhas seguidas (status diferente de 200, timeout de 10 s ou erro de rede), cerca de 4 min. Uma falha isolada não avisa.
- **Voltou:** um aviso na primeira resposta 200 depois do alerta.
- Se o Discord recusar, o estado não muda e a volta seguinte tenta de novo.

O estado fica no KV `goblinshift-uptime-state`, gravado só quando muda (durante uma queda, a cada volta, porque a contagem de falhas sobe). A URL do webhook é o secret `DISCORD_WEBHOOK_URL` do Worker, o mesmo canal de `GS_ALERT_URGENT_URL`.

## Publicar

```bash
cd deploy/uptime
npx wrangler deploy
npx wrangler secret put DISCORD_WEBHOOK_URL   # só na primeira vez
```

Testes: `node --test uptime.test.mjs` (rodam no job "Interface web" do CI).
