// Limite de requisições por conta e classe de rota (docs/GoblinShiftPlan.md, 1.7; plano 2.5).
// Espera a A2: o limite está no PR #26 (fatia-8-ratelimit, sobre a fatia 6) e ainda não existe
// na main. O run-local.sh só roda este cenário com GS_LOAD_RATE_LIMIT=1.
//
// Uma conta por classe, todas ao mesmo tempo: 120 leituras, 60 comandos e 10 POST /runs passam
// no minuto, e a requisição seguinte recebe 429 RATE_LIMITED com Retry-After. A janela é fixa,
// de um minuto em UTC, então cada rajada espera o começo de um minuto para não ser cortada pela
// virada (o relógio do k6 e o do banco são o mesmo host no Compose local).

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';
import { API, authHeaders, idempotencyKey, jsonHeaders, loadAccounts, problemCode } from './lib.js';

const ACCOUNTS = loadAccounts();
const LATEST_START_SECOND = 5;
const SECONDS_PER_MINUTE = 60;
// Qualquer id serve: a validação (?validate_only=true) não lê o upload, e a contagem vem antes.
const ANY_UPLOAD_ID = '00000000-0000-7000-8000-000000000000';

const CLASSES = {
  leitura: {
    limit: 120,
    account: 0,
    request: (token) =>
      http.get(`${API}/me`, {
        headers: authHeaders(token),
        tags: { route: 'GET /me' },
        responseCallback: http.expectedStatuses(200, 429),
      }),
  },
  comando: {
    limit: 60,
    account: 1,
    request: (token) =>
      http.post(`${API}/uploads`, JSON.stringify({ items: [{ name: 'limite.csv', size: 1 }] }), {
        headers: jsonHeaders(token),
        tags: { route: 'POST /uploads' },
        responseCallback: http.expectedStatuses(201, 429),
      }),
  },
  execucao: {
    limit: 10,
    account: 2,
    request: (token) =>
      http.post(
        `${API}/runs?validate_only=true`,
        JSON.stringify({
          recipe_id: 'csv-janitor',
          recipe_version: 1,
          input_upload_ids: [ANY_UPLOAD_ID],
          parameters: {},
        }),
        {
          headers: jsonHeaders(token, { 'Idempotency-Key': idempotencyKey() }),
          tags: { route: 'POST /runs' },
          responseCallback: http.expectedStatuses(200, 429),
        },
      ),
  },
};

const withinLimit = new Rate('gs_dentro_do_limite');
const overLimitRefused = new Rate('gs_acima_do_limite_recusado');

export const options = {
  scenarios: Object.fromEntries(
    Object.keys(CLASSES).map((name) => [
      name,
      { executor: 'per-vu-iterations', vus: 1, iterations: 1, maxDuration: '3m', env: { GS_CLASS: name } },
    ]),
  ),
  thresholds: {
    gs_dentro_do_limite: ['rate==1'],
    gs_acima_do_limite_recusado: ['rate==1'],
    http_req_failed: ['rate==0'],
  },
};

export default function () {
  const routeClass = CLASSES[__ENV.GS_CLASS];
  const token = ACCOUNTS[routeClass.account];
  waitForWindowStart();
  for (let i = 0; i < routeClass.limit; i += 1) {
    withinLimit.add(routeClass.request(token).status < 300);
  }
  const over = routeClass.request(token);
  overLimitRefused.add(
    check(over, {
      'acima do limite: 429 RATE_LIMITED com Retry-After': (r) =>
        r.status === 429 && problemCode(r) === 'RATE_LIMITED' && r.headers['Retry-After'] !== undefined,
    }),
  );
}

function waitForWindowStart() {
  const second = new Date().getUTCSeconds();
  if (second > LATEST_START_SECOND) {
    sleep(SECONDS_PER_MINUTE - second);
  }
}
