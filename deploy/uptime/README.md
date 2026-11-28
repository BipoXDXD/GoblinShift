# Monitor de uptime externo

Worker `goblinshift-uptime` da Cloudflare. A cada 2 min consulta `https://goblinshift.dev/readyz` e avisa direto no Discord, sem passar por nada na VPS: se ela cair, a telemetria cai junto, então este aviso precisa vir de fora ([3.7](../../docs/GoblinShiftPlan.md#s3-7)).

- **Fora do ar:** duas falhas seguidas (status diferente de 200, timeout de 10 s ou erro de rede), cerca de 4 min. Uma falha isolada não avisa.
- **Voltou:** um aviso na primeira resposta 200 depois do alerta.
- Se o Discord recusar, o estado não muda e a volta seguinte tenta de novo.

O estado fica no KV `goblinshift-uptime-state`, gravado só quando muda (durante uma queda, a cada volta, porque a contagem de falhas sobe). A URL do webhook é o secret `DISCORD_WEBHOOK_URL` do Worker, o mesmo canal de `GS_ALERT_URGENT_URL`.

## Quem vigia o vigia

O heartbeat do OpenObserve (`gs-heartbeat`) prova que ele alcança o healthchecks.io, não que o Discord aceita os avisos. Como os avisos `URGENT` do OpenObserve vão para o mesmo canal, um webhook revogado calaria os dois sem ninguém perceber. Por isso:

- **Ping de vida (dead-man's switch).** Com o secret `HEALTHCHECK_PING_URL`, o Worker pinga o check `uptime Worker` do healthchecks.io no fim de toda volta em que o Discord não recusou (inclusive as voltas sem aviso), e `<url>/fail` quando recusou, antes de relançar o erro. O Worker parado, o cron desligado ou o Discord recusando viram alarme pelo canal do healthchecks.io. A falha do próprio ping não muda nada: sem o ping no prazo, o healthchecks.io avisa sozinho. A URL é segredo (quem a tem cala o alarme) e não vai para log nem para mensagem de erro. Sem o secret, nenhum ping sai e o resto funciona igual.
- **Registro do não entregue.** Quando o Discord recusa (status diferente de 2xx, timeout ou erro de rede), o Worker grava no KV a chave `undelivered` = `{count, firstAt, lastAt, lastStatus, lastError}`: quantas recusas seguidas, a primeira e a última (ISO 8601, UTC) e como foi a última: `lastStatus` é o status HTTP da recusa (`null` em timeout ou erro de rede) e `lastError` é o nome do erro nesses casos, como `TimeoutError` (`null` quando houve resposta HTTP). Se o próprio KV falhar ao gravar, o `/fail` sai do mesmo jeito. A primeira entrega depois disso apaga a chave. Como o aviso pendente se repete a cada volta, são no máximo 720 escritas por dia. Para ler:

  ```bash
  cd deploy/uptime
  npx wrangler kv key get undelivered --binding UPTIME_STATE   # vazio: tudo entregue
  ```

## Publicar

```bash
cd deploy/uptime
npx wrangler deploy
npx wrangler secret put DISCORD_WEBHOOK_URL    # só na primeira vez
npx wrangler secret put HEALTHCHECK_PING_URL   # só na primeira vez; URL de ping do check "uptime Worker"
```

### Ligar o ping de vida (operador, uma vez)

Feito pelo dono, com autorização (#442); até lá o Worker roda sem a variável e nada muda.

1. No healthchecks.io, criar o check `uptime Worker`: período 2 min, tolerância 10 min, mesmo canal de alerta dos outros checks (tabela em [`deploy/openobserve/README.md`](../openobserve/README.md#monitores-externos)).
2. `cd deploy/uptime && npx wrangler secret put HEALTHCHECK_PING_URL` com a URL de ping do check, e `npx wrangler deploy`.
3. Recibo: o check fica "up" no painel em até 2 min, e `npx wrangler kv key get undelivered --binding UPTIME_STATE` sai vazio.

Testes: `node --test uptime.test.mjs` (rodam no job "Interface web" do CI).
