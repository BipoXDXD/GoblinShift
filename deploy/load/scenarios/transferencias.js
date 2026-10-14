// Transferências simultâneas (docs/GoblinShiftPlan.md, 1.7; plano 2.5): uma conta envia três
// arquivos ao mesmo tempo. Cabem 2 por conta (MAX_TRANSFERS_PER_ACCOUNT em
// src/goblinshift/files/limits.py); o envio sem vaga recebe 429 RATE_LIMITED com Retry-After.
// Na rede local um envio termina em dezenas de milissegundos, então o cenário repete a rajada
// com arquivos grandes para que os três se sobreponham; basta uma recusa para provar o limite,
// e nenhuma resposta pode ser outra coisa além de 200 ou dessa recusa.

import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
import { API, createIntents, loadAccounts, problemCode, putContentParams } from './lib.js';

const SIMULTANEOUS = 3;
const ROUNDS = Number(__ENV.GS_LOAD_TRANSFER_ROUNDS || 5);
const FILE_BYTES = 24 * 1024 * 1024;
const ACCOUNTS = loadAccounts();
// A última conta: as primeiras ficam para a fila e a jornada.
const TOKEN = ACCOUNTS[ACCOUNTS.length - 1];
const BODY = 'x'.repeat(FILE_BYTES);

const limited = new Counter('gs_transferencia_limitada');
const unexpected = new Counter('gs_transferencia_inesperada');

export const options = {
  scenarios: {
    transferencias: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: ROUNDS,
      maxDuration: '3m',
    },
  },
  thresholds: {
    gs_transferencia_limitada: ['count>=1'],
    gs_transferencia_inesperada: ['count==0'],
    http_req_failed: ['rate==0'],
  },
};

export default function () {
  unexpected.add(0);
  const items = Array.from({ length: SIMULTANEOUS }, (_, i) => ({
    name: `transferencia-${__ITER}-${i}.bin`,
    size: FILE_BYTES,
  }));
  const ids = createIntents(TOKEN, items);
  const params = { ...putContentParams(TOKEN), responseCallback: http.expectedStatuses(200, 429) };
  const responses = http.batch(
    ids.map((id) => ['PUT', `${API}/uploads/${id}/content`, BODY, params]),
  );
  for (const response of responses) {
    if (response.status === 200) {
      continue;
    }
    const refused = check(response, {
      'sem vaga é 429 RATE_LIMITED com Retry-After': (r) =>
        r.status === 429 && problemCode(r) === 'RATE_LIMITED' && r.headers['Retry-After'] !== undefined,
    });
    (refused ? limited : unexpected).add(1);
  }
}
