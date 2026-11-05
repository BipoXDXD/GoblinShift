import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkOnce } from './worker.mjs';

const TARGET = 'https://exemplo.test/readyz';
const WEBHOOK = 'https://discord.test/webhook/SEGREDO-DO-CANAL';
const MINUTE_MS = 60_000;

function harness(responses) {
  const store = new Map();
  const sent = [];
  const probes = [];
  const discord = { ok: true };
  let writes = 0;
  const queue = [...responses];
  const fetcher = async (url, init) => {
    if (url === WEBHOOK) {
      assert.ok(init.signal instanceof AbortSignal);
      sent.push(JSON.parse(init.body).content);
      return { ok: discord.ok, status: discord.ok ? 204 : 500 };
    }
    probes.push(init);
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
  return { env, fetcher, sent, probes, discord, writes: () => writes };
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

test('se o Discord falhar, a queda é avisada na volta seguinte', async () => {
  const h = harness([503, 503, 503]);
  await checkOnce(h.env, h.fetcher, 0);
  h.discord.ok = false;
  await assert.rejects(checkOnce(h.env, h.fetcher, MINUTE_MS), /HTTP 500/);
  h.discord.ok = true;
  await checkOnce(h.env, h.fetcher, 2 * MINUTE_MS);
  assert.equal(h.sent.length, 2);
});

test('se o Discord falhar, a volta é avisada na volta seguinte', async () => {
  const h = harness([503, 503, 200, 200]);
  await checkOnce(h.env, h.fetcher, 0);
  await checkOnce(h.env, h.fetcher, MINUTE_MS);
  h.discord.ok = false;
  await assert.rejects(checkOnce(h.env, h.fetcher, 2 * MINUTE_MS), /HTTP 500/);
  h.discord.ok = true;
  await checkOnce(h.env, h.fetcher, 3 * MINUTE_MS);
  assert.match(h.sent.at(-1), /VOLTOU/);
});

test('a URL do webhook não aparece no erro', async () => {
  const h = harness([503, 503]);
  await checkOnce(h.env, h.fetcher, 0);
  h.discord.ok = false;
  await assert.rejects(checkOnce(h.env, h.fetcher, MINUTE_MS), (error) => {
    assert.ok(!error.message.includes('SEGREDO-DO-CANAL'));
    return true;
  });
});

test('a sonda não segue redirecionamento e tem timeout', async () => {
  const h = harness([200]);
  await checkOnce(h.env, h.fetcher, 0);
  assert.equal(h.probes[0].redirect, 'manual');
  assert.ok(h.probes[0].signal instanceof AbortSignal);
});

test('204 não conta como saudável: o /readyz responde 200', async () => {
  const h = harness([204, 204]);
  await checkOnce(h.env, h.fetcher, 0);
  await checkOnce(h.env, h.fetcher, MINUTE_MS);
  assert.equal(h.sent.length, 1);
});
