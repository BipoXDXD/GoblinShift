// Monitor de uptime externo (docs/GoblinShiftPlan.md, 3.7): roda fora da VPS, porque a telemetria
// cai junto com ela, e avisa direto no Discord, sem passar por nada que dependa da VPS.
import { INITIAL_STATE, advance, describe } from './uptime.mjs';

const STATE_KEY = 'readyz';
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
    await sendToDiscord(env.DISCORD_WEBHOOK_URL, content, fetcher);
  }
  if (JSON.stringify(state) !== JSON.stringify(previous)) {
    await env.UPTIME_STATE.put(STATE_KEY, JSON.stringify(state));
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
    throw new Error(`Discord recusou a notificação: HTTP ${response.status}`);
  }
}
