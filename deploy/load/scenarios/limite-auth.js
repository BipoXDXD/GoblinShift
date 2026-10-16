// Limite das rotas /auth/* no Nginx (docs/GoblinShiftPlan.md, 3.8; plano 2.5).
// O `limit_req` de /auth/ veio com a fatia 6 (PR #27). O run-local.sh roda este cenário por padrão;
// GS_LOAD_AUTH=0 o desliga.
//
// O Nginx aceita 10 por minuto por cliente com rajada de 10 sem atraso: 11 seguidas passam e a
// 12ª recebe 429 RATE_LIMITED com Retry-After, no mesmo problem+json da API. Sem conta: o login
// começa sem autenticação, e o que importa é a recusa vir do Nginx antes da API.

import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';
import { BASE_URL, problemCode } from './lib.js';

const ALLOWED_IN_BURST = 11;
const AUTH_PATH = __ENV.GS_LOAD_AUTH_PATH || '/auth/github/login';

const withinBurst = new Rate('gs_auth_dentro_da_rajada');
const overBurstRefused = new Rate('gs_auth_acima_recusado');

export const options = {
  scenarios: {
    auth: { executor: 'per-vu-iterations', vus: 1, iterations: 1, maxDuration: '1m' },
  },
  thresholds: {
    gs_auth_dentro_da_rajada: ['rate==1'],
    gs_auth_acima_recusado: ['rate==1'],
  },
};

export default function () {
  const params = { redirects: 0, tags: { route: 'GET /auth/*' } };
  for (let i = 0; i < ALLOWED_IN_BURST; i += 1) {
    const response = http.get(`${BASE_URL}${AUTH_PATH}`, params);
    withinBurst.add(response.status !== 429 && response.status < 500);
  }
  const over = http.get(`${BASE_URL}${AUTH_PATH}`, params);
  overBurstRefused.add(
    check(over, {
      'acima da rajada: 429 RATE_LIMITED com Retry-After': (r) =>
        r.status === 429 && problemCode(r) === 'RATE_LIMITED' && r.headers['Retry-After'] !== undefined,
    }),
  );
}
