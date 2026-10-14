// Jornada realista (docs/GoblinShiftPlan.md, fatia 8; plano 2.5): cada VU é uma conta que envia
// uma tabela, cria uma execução do csv-janitor, consulta GET /runs/{id} a cada 2 s até o fim e
// baixa o primeiro artefato. Repete o ciclo até o fim do cenário.
//
// Os thresholds são os SLOs de deploy/openobserve (tests/test_load_script.py confere os números):
// - aceitação: POST /runs com 202 em menos de 500 ms em 99,5% dos casos;
// - execução: 99% das Runs terminam bem em até 5 min. Aqui o tempo vai do 202 até a consulta que
//   vê o estado terminal, então inclui a fila e o intervalo de consulta: é mais severo que o SLO;
// - download: primeiro byte em menos de 1 s em 99,5% dos downloads;
// - consulta: GET /runs/{id} com p99 abaixo de 500 ms (critério do plano 2.5 para a rampa).
// A fila global cheia (503 QUEUE_FULL) é o serviço se protegendo e não conta como falha, como no
// SLO de aceitação; o cliente espera e tenta de novo.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';
import {
  API,
  SAMPLE_CSV,
  authHeaders,
  csvRunBody,
  idempotencyKey,
  jsonHeaders,
  loadAccounts,
  problemCode,
  uploadReady,
} from './lib.js';

const ACCOUNTS = loadAccounts();
const POLL_INTERVAL_SECONDS = 2;
// Menor que o Retry-After de 60 s da fila cheia: o teste dura poucos minutos.
const QUEUE_FULL_BACKOFF_SECONDS = 5;
const EXECUTION_SLO_MS = 300000;
const TERMINAL = new Set(['succeeded', 'succeeded_with_warnings', 'canceled', 'failed']);
const SUCCEEDED = new Set(['succeeded', 'succeeded_with_warnings']);
const VUS = Math.min(ACCOUNTS.length, Number(__ENV.GS_LOAD_VUS || 20));
const HOLD = __ENV.GS_LOAD_HOLD || '1m40s';

const goodExecution = new Rate('gs_execucao_boa');

export const options = {
  scenarios: {
    jornada: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '20s', target: VUS },
        { duration: HOLD, target: VUS },
        { duration: '10s', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    'http_req_duration{route:POST /runs,status:202}': ['p(99.5)<500'],
    'http_req_duration{route:GET /runs/{id}}': ['p(99)<500'],
    'http_req_waiting{route:GET /artifacts/{id}/download,status:200}': ['p(99.5)<1000'],
    gs_execucao_boa: ['rate>=0.99'],
    http_req_failed: ['rate<0.005'],
    checks: ['rate>0.995'],
  },
};

// Estado por VU: o k6 isola as variáveis de módulo em cada VU.
let uploadId = null;
let current = null;

export default function () {
  const token = ACCOUNTS[(__VU - 1) % ACCOUNTS.length];
  if (uploadId === null) {
    uploadId = uploadReady(token, `jornada-${__VU}.csv`, SAMPLE_CSV);
  }
  if (current === null) {
    current = createRun(token);
    if (current === null) {
      sleep(QUEUE_FULL_BACKOFF_SECONDS);
      return;
    }
  }
  sleep(POLL_INTERVAL_SECONDS);
  pollRun(token);
}

function createRun(token) {
  const response = http.post(`${API}/runs`, csvRunBody(uploadId), {
    headers: jsonHeaders(token, { 'Idempotency-Key': idempotencyKey() }),
    tags: { route: 'POST /runs' },
    responseCallback: http.expectedStatuses(202, 503),
  });
  if (response.status === 503) {
    check(response, {
      'fila cheia é QUEUE_FULL com Retry-After': (r) =>
        problemCode(r) === 'QUEUE_FULL' && r.headers['Retry-After'] !== undefined,
    });
    return null;
  }
  check(response, { 'POST /runs aceito': (r) => r.status === 202 });
  return response.status === 202 ? { id: response.json('id'), acceptedAt: Date.now() } : null;
}

function pollRun(token) {
  const response = http.get(`${API}/runs/${current.id}`, {
    headers: authHeaders(token),
    tags: { route: 'GET /runs/{id}' },
  });
  if (!check(response, { 'GET /runs/{id} 200': (r) => r.status === 200 })) {
    return;
  }
  const status = response.json('status');
  if (!TERMINAL.has(status)) {
    return;
  }
  const elapsed = Date.now() - current.acceptedAt;
  goodExecution.add(SUCCEEDED.has(status) && elapsed <= EXECUTION_SLO_MS);
  if (SUCCEEDED.has(status)) {
    downloadFirstArtifact(token, current.id);
  }
  current = null;
}

function downloadFirstArtifact(token, runId) {
  const results = http.get(`${API}/runs/${runId}/results`, {
    headers: authHeaders(token),
    tags: { route: 'GET /runs/{id}/results' },
  });
  const ok = check(results, {
    'resultados publicados': (r) => r.status === 200 && r.json('published') === true,
  });
  if (!ok) {
    return;
  }
  const artifactId = results.json('artifacts.0.artifact_id');
  const download = http.get(`${API}/artifacts/${artifactId}/download`, {
    headers: authHeaders(token),
    tags: { route: 'GET /artifacts/{id}/download' },
  });
  check(download, {
    'download completo': (r) => r.status === 200 && r.body !== null && r.body.length > 0,
  });
}
