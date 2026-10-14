// Funções comuns aos cenários de carga (deploy/load/README.md).
// As contas vêm de um arquivo JSON montado pelo run-local.sh: uma lista de chaves de API emitidas
// pelo gs-admin, nunca escritas no repositório nem no relatório.

import http from 'k6/http';
import { randomBytes } from 'k6/crypto';

export const BASE_URL = (__ENV.GS_LOAD_TARGET || 'http://nginx').replace(/\/+$/, '');
export const API = `${BASE_URL}/api/v1`;

const ACCOUNTS_FILE = __ENV.GS_LOAD_ACCOUNTS || '/accounts/accounts.json';

// Lido no contexto de inicialização, como o k6 exige para `open`.
export function loadAccounts() {
  const tokens = JSON.parse(open(ACCOUNTS_FILE));
  if (!Array.isArray(tokens) || tokens.length === 0) {
    throw new Error(`nenhuma conta em ${ACCOUNTS_FILE}`);
  }
  return tokens;
}

export function authHeaders(token, extra = {}) {
  return { Authorization: `Bearer ${token}`, ...extra };
}

export function jsonHeaders(token, extra = {}) {
  return authHeaders(token, { 'Content-Type': 'application/json', ...extra });
}

// Idempotency-Key aceita: de 16 a 64 caracteres [A-Za-z0-9_-] (docs/GoblinShiftPlan.md, 1.6).
export function idempotencyKey() {
  const bytes = new Uint8Array(randomBytes(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export function problemCode(response) {
  try {
    return response.json('code');
  } catch (_) {
    return null;
  }
}

// Cria uma intenção de upload e envia o conteúdo; devolve o id do upload pronto.
export function uploadReady(token, name, body) {
  const created = http.post(
    `${API}/uploads`,
    JSON.stringify({ items: [{ name, size: body.length }] }),
    { headers: jsonHeaders(token), tags: { route: 'POST /uploads' } },
  );
  if (created.status !== 201) {
    throw new Error(`POST /uploads devolveu ${created.status}: ${created.body}`);
  }
  const id = created.json('items.0.id');
  const sent = putContent(token, id, body);
  if (sent.status !== 200) {
    throw new Error(`PUT do conteúdo devolveu ${sent.status}: ${sent.body}`);
  }
  return id;
}

export function createIntents(token, items) {
  const created = http.post(`${API}/uploads`, JSON.stringify({ items }), {
    headers: jsonHeaders(token),
    tags: { route: 'POST /uploads' },
  });
  if (created.status !== 201) {
    throw new Error(`POST /uploads devolveu ${created.status}: ${created.body}`);
  }
  return created.json('items').map((item) => item.id);
}

export function putContentParams(token, extraTags = {}) {
  return {
    headers: authHeaders(token, { 'Content-Type': 'application/octet-stream' }),
    tags: { route: 'PUT /uploads/{id}/content', ...extraTags },
  };
}

export function putContent(token, uploadId, body) {
  return http.put(`${API}/uploads/${uploadId}/content`, body, putContentParams(token));
}

// Comando de uma execução do csv-janitor com os defaults da versão 1.
export function csvRunBody(uploadId) {
  return JSON.stringify({
    recipe_id: 'csv-janitor',
    recipe_version: 1,
    input_upload_ids: [uploadId],
    parameters: {},
  });
}

export function cancelRun(token, runId) {
  return http.post(`${API}/runs/${runId}/cancel`, null, {
    headers: authHeaders(token),
    tags: { route: 'POST /runs/{id}/cancel' },
  });
}

export const SAMPLE_CSV = 'nome,email\nAna,ana@example.com\nBeto,beto@example.com\nAna,ana@example.com\n';
