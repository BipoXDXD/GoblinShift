// Monitor de uptime externo (docs/GoblinShiftPlan.md, 3.7): roda fora da VPS, porque a telemetria
// cai junto com ela, e avisa direto no Discord, sem passar por nada que dependa da VPS.
//
// Dead-man's switch (D15): com o secret HEALTHCHECK_PING_URL, cada volta em que o Discord não
// recusou pinga o check "uptime Worker" do healthchecks.io, e a recusa pinga /fail. O Worker
// parado ou o Discord recusando (o mesmo canal dos avisos URGENT do OpenObserve) viram alarme por
// outro caminho. Sem o secret, nada sai. A recusa também fica no KV, na chave `undelivered`.
import { INITIAL_STATE, advance, describe } from './uptime.mjs';

const STATE_KEY = 'readyz';
const UNDELIVERED_KEY = 'undelivered';
const PROBE_TIMEOUT_MS = 10_000;
const HTTP_OK = 200;

export default {
  async scheduled(_event, env) {
    await checkOnce(env, fetch, Date.now());
  },
};

export async function checkOnce(env, fetcher, nowMs) {
  const previous = (await env.UPTIME_STATE.get(STATE_KEY, 'json')) ?? INITIAL_STATE;
  const probe = await probeTarget(env.TARGET_URL, fetcher);
  const { state, notify } = advance(previous, probe.healthy, nowMs);
  if (notify !== null) {
    // Antes de gravar: se o Discord falhar, o estado não muda e a próxima volta tenta de novo.
    const content = describe(notify, previous, probe.detail, nowMs, env.TARGET_URL);
    try {
      await sendToDiscord(env.DISCORD_WEBHOOK_URL, content, fetcher);
    } catch (error) {
      await recordUndelivered(env.UPTIME_STATE, error, nowMs);
      await pingHealthcheck(env.HEALTHCHECK_PING_URL, '/fail', fetcher);
      throw error;
    }
    await clearUndelivered(env.UPTIME_STATE);
  }
  if (JSON.stringify(state) !== JSON.stringify(previous)) {
    await env.UPTIME_STATE.put(STATE_KEY, JSON.stringify(state));
  }
  await pingHealthcheck(env.HEALTHCHECK_PING_URL, '', fetcher);
}

// Uma escrita por recusa: o Discord só é chamado quando há aviso, e a volta seguinte repete o
// mesmo aviso, então são no máximo 720 escritas por dia, abaixo do limite do KV gratuito.
async function recordUndelivered(kv, error, nowMs) {
  const previous = await kv.get(UNDELIVERED_KEY, 'json');
  const at = new Date(nowMs).toISOString();
  const record = {
    count: (previous?.count ?? 0) + 1,
    firstAt: previous?.firstAt ?? at,
    lastAt: at,
    lastStatus: error.status ?? error.name,
  };
  await kv.put(UNDELIVERED_KEY, JSON.stringify(record));
}

async function clearUndelivered(kv) {
  if ((await kv.get(UNDELIVERED_KEY, 'json')) !== null) {
    await kv.delete(UNDELIVERED_KEY);
  }
}

// O ping nunca muda o desfecho da volta: sem ele, o próprio healthchecks.io avisa. A URL é segredo
// (quem a tem cala o alarme) e não vai para log nem para mensagem de erro.
async function pingHealthcheck(pingUrl, suffix, fetcher) {
  if (!pingUrl) return;
  try {
    const response = await fetcher(`${pingUrl.replace(/\/+$/, '')}${suffix}`, {
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    if (!response.ok) console.warn(`ping ao healthchecks.io recusado: HTTP ${response.status}`);
  } catch (error) {
    console.warn(`ping ao healthchecks.io falhou: ${error.name}`);
  }
}

async function probeTarget(url, fetcher) {
  try {
    const response = await fetcher(url, {
      redirect: 'manual',
      signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    return { healthy: response.status === HTTP_OK, detail: `HTTP ${response.status}` };
  } catch (error) {
    return { healthy: false, detail: error.name };
  }
}

async function sendToDiscord(webhookUrl, content, fetcher) {
  const response = await fetcher(webhookUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content }),
    signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
  });
  if (!response.ok) {
    // Sem a URL na mensagem: quem tem o webhook publica no canal.
    const error = new Error(`Discord recusou a notificação: HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
}
