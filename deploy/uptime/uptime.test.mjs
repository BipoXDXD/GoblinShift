import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkOnce } from './worker.mjs';

const TARGET = 'https://exemplo.test/readyz';
const WEBHOOK = 'https://discord.test/webhook/SEGREDO-DO-CANAL';
const PING = 'https://hc-ping.test/SEGREDO-DO-CHECK';
const MINUTE_MS = 60_000;

function harness(responses, { pingUrl } = {}) {
  const store = new Map();
  const sent = [];
  const probes = [];
  const pings = [];
  const discord = { ok: true, status: 500 };
  const healthcheck = { error: null };
  let writes = 0;
  const queue = [...responses];
  const fetcher = async (url, init) => {
    if (url === WEBHOOK) {
      assert.ok(init.signal instanceof AbortSignal);
      sent.push(JSON.parse(init.body).content);
      return { ok: discord.ok, status: discord.ok ? 204 : discord.status };
    }
    if (url.startsWith(PING)) {
      assert.ok(init.signal instanceof AbortSignal);
      pings.push(url.slice(PING.length) || 'sucesso');
      if (healthcheck.error !== null) throw healthcheck.error;
      return { ok: true, status: 200 };
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
      delete: async (key) => {
        writes += 1;
        store.delete(key);
      },
    },
  };
  if (pingUrl !== undefined) env.HEALTHCHECK_PING_URL = pingUrl;
  const undelivered = () =>
    store.has('undelivered') ? JSON.parse(store.get('undelivered')) : null;
  const writeCount = () => writes;
  return {
    env,
    fetcher,
    sent,
    probes,
    pings,
    discord,
    healthcheck,
    undelivered,
    writes: writeCount,
  };
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

test('Discord com 404 manda o ping para /fail e relança', async () => {
  const h = harness([503, 503], { pingUrl: PING });
  await checkOnce(h.env, h.fetcher, 0);
  h.discord.ok = false;
  h.discord.status = 404;
  await assert.rejects(checkOnce(h.env, h.fetcher, MINUTE_MS), /HTTP 404/);
  assert.deepEqual(h.pings, ['sucesso', '/fail']);
});

test('volta saudável manda o ping de sucesso', async () => {
  const h = harness([200], { pingUrl: PING });
  await checkOnce(h.env, h.fetcher, 0);
  assert.deepEqual(h.pings, ['sucesso']);
});

test('aviso entregue também manda o ping de sucesso', async () => {
  const h = harness([503, 503], { pingUrl: `${PING}/` });
  await checkOnce(h.env, h.fetcher, 0);
  await checkOnce(h.env, h.fetcher, MINUTE_MS);
  assert.equal(h.sent.length, 1);
  assert.deepEqual(h.pings, ['sucesso', 'sucesso']);
});

test('sem HEALTHCHECK_PING_URL nenhum ping sai', async () => {
  const h = harness([503, 503]);
  await checkOnce(h.env, h.fetcher, 0);
  h.discord.ok = false;
  await assert.rejects(checkOnce(h.env, h.fetcher, MINUTE_MS), /HTTP 500/);
  assert.deepEqual(h.pings, []);
});

async function refuseTwice(h) {
  await checkOnce(h.env, h.fetcher, 0);
  h.discord.ok = false;
  h.discord.status = 404;
  await assert.rejects(checkOnce(h.env, h.fetcher, MINUTE_MS), /HTTP 404/);
  h.discord.status = 429;
  await assert.rejects(checkOnce(h.env, h.fetcher, 2 * MINUTE_MS), /HTTP 429/);
}

test('recusas seguidas somam no registro undelivered', async () => {
  const h = harness([503, 503, 503]);
  await refuseTwice(h);
  assert.deepEqual(h.undelivered(), {
    count: 2,
    firstAt: new Date(MINUTE_MS).toISOString(),
    lastAt: new Date(2 * MINUTE_MS).toISOString(),
    lastStatus: 429,
    lastError: null,
  });
});

test('a primeira entrega apaga o registro undelivered', async () => {
  const h = harness([503, 503, 503, 503]);
  await refuseTwice(h);
  h.discord.ok = true;
  await checkOnce(h.env, h.fetcher, 3 * MINUTE_MS);
  assert.equal(h.undelivered(), null);
});

test('erro de rede no Discord fica no registro undelivered sem status', async () => {
  const h = harness([503, 503]);
  await checkOnce(h.env, h.fetcher, 0);
  const fetcher = async (url, init) => {
    if (url === WEBHOOK) throw new DOMException('t', 'TimeoutError');
    return h.fetcher(url, init);
  };
  await assert.rejects(checkOnce(h.env, fetcher, MINUTE_MS), { name: 'TimeoutError' });
  assert.equal(h.undelivered().lastStatus, null);
  assert.equal(h.undelivered().lastError, 'TimeoutError');
});

test('KV fora do ar na recusa: o /fail sai e o erro do Discord é o propagado', async () => {
  const h = harness([503, 503], { pingUrl: PING });
  await checkOnce(h.env, h.fetcher, 0);
  h.discord.ok = false;
  h.discord.status = 404;
  h.env.UPTIME_STATE.put = async () => {
    throw new Error('KV fora do ar');
  };
  await assert.rejects(checkOnce(h.env, h.fetcher, MINUTE_MS), /HTTP 404/);
  assert.deepEqual(h.pings, ['sucesso', '/fail']);
});

test('entrega sem registro undelivered não grava nada a mais', async () => {
  const h = harness([503, 503]);
  await checkOnce(h.env, h.fetcher, 0);
  const before = h.writes();
  await checkOnce(h.env, h.fetcher, MINUTE_MS);
  assert.equal(h.writes() - before, 1);
});

test('falha do próprio ping não esconde o erro do Discord', async () => {
  const h = harness([503, 503], { pingUrl: PING });
  await checkOnce(h.env, h.fetcher, 0);
  h.discord.ok = false;
  h.discord.status = 404;
  h.healthcheck.error = new TypeError('rede');
  await assert.rejects(checkOnce(h.env, h.fetcher, MINUTE_MS), /HTTP 404/);
  assert.deepEqual(h.pings, ['sucesso', '/fail']);
});

test('falha do ping de sucesso não derruba a volta', async () => {
  const h = harness([200], { pingUrl: PING });
  h.healthcheck.error = new TypeError('rede');
  await checkOnce(h.env, h.fetcher, 0);
  assert.deepEqual(h.pings, ['sucesso']);
});

test('a URL do ping não aparece no erro', async () => {
  const h = harness([503, 503], { pingUrl: PING });
  await checkOnce(h.env, h.fetcher, 0);
  h.discord.ok = false;
  h.healthcheck.error = new TypeError(`falhou ${PING}`);
  await assert.rejects(checkOnce(h.env, h.fetcher, MINUTE_MS), (error) => {
    assert.ok(!error.message.includes('SEGREDO-DO-CHECK'));
    return true;
  });
});
