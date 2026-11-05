import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkOnce } from './worker.mjs';

const TARGET = 'https://exemplo.test/readyz';
const WEBHOOK = 'https://discord.test/webhook';
const MINUTE_MS = 60_000;

function harness(responses) {
  const store = new Map();
  const sent = [];
  let writes = 0;
  const queue = [...responses];
  const fetcher = async (url, init) => {
    if (url === WEBHOOK) {
      sent.push(JSON.parse(init.body).content);
      return { ok: harness.discordOk ?? true, status: harness.discordOk === false ? 500 : 204 };
    }
    const next = queue.shift();
    if (next instanceof Error) throw next;
    return { status: next };
  };
  const env = {
    TARGET_URL: TARGET,
    DISCORD_WEBHOOK_URL: WEBHOOK,
    UPTIME_STATE: {
      get: async (key) => (store.has(key) ? JSON.parse(store.get(key)) : null),
      put: async (key, value) => {
        writes += 1;
        store.set(key, value);
      },
    },
  };
  return { env, fetcher, sent, writes: () => writes };
}

test('serviço saudável não avisa nem grava', async () => {
  const h = harness([200, 200]);
  await checkOnce(h.env, h.fetcher, 0);
  await checkOnce(h.env, h.fetcher, MINUTE_MS);
  assert.deepEqual(h.sent, []);
  assert.equal(h.writes(), 0);
});

test('uma falha isolada não avisa', async () => {
  const h = harness([503, 200]);
  await checkOnce(h.env, h.fetcher, 0);
  await checkOnce(h.env, h.fetcher, MINUTE_MS);
  assert.deepEqual(h.sent, []);
});

test('a segunda falha seguida avisa uma vez, com o status', async () => {
  const h = harness([503, 503, 503]);
  await checkOnce(h.env, h.fetcher, 0);
  await checkOnce(h.env, h.fetcher, MINUTE_MS);
  await checkOnce(h.env, h.fetcher, 2 * MINUTE_MS);
  assert.deepEqual(h.sent, [`[GoblinShift] FORA DO AR: ${TARGET} respondeu HTTP 503`]);
});

test('timeout ou erro de rede conta como falha', async () => {
  const h = harness([new DOMException('t', 'TimeoutError'), new TypeError('rede')]);
  await checkOnce(h.env, h.fetcher, 0);
  await checkOnce(h.env, h.fetcher, MINUTE_MS);
  assert.deepEqual(h.sent, [`[GoblinShift] FORA DO AR: ${TARGET} respondeu TypeError`]);
});

test('redirecionamento não conta como saudável', async () => {
  const h = harness([302, 302]);
  await checkOnce(h.env, h.fetcher, 0);
  await checkOnce(h.env, h.fetcher, MINUTE_MS);
  assert.equal(h.sent.length, 1);
});

test('avisa ao voltar, com o tempo desde o aviso, e zera o estado', async () => {
  const h = harness([503, 503, 200, 503]);
  await checkOnce(h.env, h.fetcher, 0);
  await checkOnce(h.env, h.fetcher, 2 * MINUTE_MS);
  await checkOnce(h.env, h.fetcher, 12 * MINUTE_MS);
  await checkOnce(h.env, h.fetcher, 14 * MINUTE_MS);
  assert.equal(h.sent.length, 2);
  assert.equal(h.sent[1], `[GoblinShift] VOLTOU: ${TARGET} responde 200 (cerca de 10 min desde o aviso)`);
});

test('se o Discord falhar, o estado não muda e a próxima volta tenta de novo', async () => {
  const h = harness([503, 503, 503]);
  await checkOnce(h.env, h.fetcher, 0);
  harness.discordOk = false;
  await assert.rejects(checkOnce(h.env, h.fetcher, MINUTE_MS), /HTTP 500/);
  harness.discordOk = true;
  await checkOnce(h.env, h.fetcher, 2 * MINUTE_MS);
  assert.equal(h.sent.length, 2);
  delete harness.discordOk;
});
