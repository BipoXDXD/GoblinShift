// Fila global cheia (docs/GoblinShiftPlan.md, 1.7; plano 2.5): com a fila vazia e o worker parado,
// 12 contas pedem uma execução ao mesmo tempo. Cabem 10 aguardando (MAX_QUEUED_RUNS em
// src/goblinshift/runs/acceptance.py); as demais recebem 503 QUEUE_FULL com Retry-After, nunca
// outro erro. O run-local.sh para o worker antes e o religa depois; o teardown cancela o que entrou.

import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
import {
  API,
  SAMPLE_CSV,
  authHeaders,
  cancelRun,
  csvRunBody,
  idempotencyKey,
  jsonHeaders,
  loadAccounts,
  problemCode,
  uploadReady,
} from './lib.js';

const MAX_QUEUED_RUNS = 10;
const CONTENDERS = MAX_QUEUED_RUNS + 2;
const ACCOUNTS = loadAccounts().slice(0, CONTENDERS);
if (ACCOUNTS.length < CONTENDERS) {
  throw new Error(`a fila precisa de ${CONTENDERS} contas; há ${ACCOUNTS.length}`);
}

const accepted = new Counter('gs_fila_aceitas');
const queueFull = new Counter('gs_fila_cheia');
const unexpected = new Counter('gs_fila_inesperadas');

export const options = {
  scenarios: {
    fila: { executor: 'per-vu-iterations', vus: CONTENDERS, iterations: 1, maxDuration: '1m' },
  },
  thresholds: {
    gs_fila_aceitas: [`count<=${MAX_QUEUED_RUNS}`],
    gs_fila_cheia: ['count>=1'],
    gs_fila_inesperadas: ['count==0'],
    http_req_failed: ['rate==0'],
  },
};

export function setup() {
  return ACCOUNTS.map((token, index) => uploadReady(token, `fila-${index}.csv`, SAMPLE_CSV));
}

export default function (uploadIds) {
  const index = __VU - 1;
  const token = ACCOUNTS[index];
  unexpected.add(0);
  const response = http.post(`${API}/runs`, csvRunBody(uploadIds[index]), {
    headers: jsonHeaders(token, { 'Idempotency-Key': idempotencyKey() }),
    tags: { route: 'POST /runs' },
    responseCallback: http.expectedStatuses(202, 503),
  });
  if (response.status === 202) {
    accepted.add(1);
    return;
  }
  const full = check(response, {
    'recusa é 503 QUEUE_FULL com Retry-After': (r) =>
      r.status === 503 && problemCode(r) === 'QUEUE_FULL' && r.headers['Retry-After'] !== undefined,
  });
  (full ? queueFull : unexpected).add(1);
}

export function teardown() {
  for (const token of ACCOUNTS) {
    const queued = http.get(`${API}/runs?status=queued`, {
      headers: authHeaders(token),
      tags: { route: 'GET /runs' },
    });
    for (const run of queued.json('data') || []) {
      cancelRun(token, run.id);
    }
  }
}
