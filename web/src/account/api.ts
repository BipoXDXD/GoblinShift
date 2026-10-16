import { z } from "zod";

import type { ApiClient } from "../api/client";
import type { operations } from "../api/schema";

// Espelhos em runtime dos schemas da conta na spec; api.test.ts trava a igualdade com os tipos
// gerados. As rotas de /auth ficam fora do OpenAPI (auth_session.py) e respondem 204 sem corpo.
export const SCOPES = ["files:read", "files:write", "runs:read", "runs:write"] as const;
const instant = z.iso.datetime({ offset: true });

const meSchema = z.object({
  id: z.uuid(),
  display_name: z.string(),
  scopes: z.array(z.enum(SCOPES)),
  via: z.enum(["session", "api_key"]),
  limits: z.object({
    runs_per_24h: z.number().int(),
    input_bytes_per_24h: z.number().int(),
    storage_bytes: z.number().int(),
    active_runs: z.number().int(),
    file_bytes: z.number().int(),
    batch_files: z.number().int(),
    batch_bytes: z.number().int(),
    concurrent_uploads: z.number().int(),
    concurrent_downloads: z.number().int(),
  }),
  usage: z.object({
    runs_last_24h: z.number().int(),
    input_bytes_last_24h: z.number().int(),
    storage_reserved_bytes: z.number().int(),
    active_run_id: z.uuid().nullable(),
    daily_quota_resets_at: instant.nullable(),
    executor_ms_last_24h: z.number().int(),
  }),
  terms: z.object({
    current_version: z.string(),
    accepted_version: z.string().nullable(),
    accepted_at: instant.nullable(),
    acceptance_required: z.boolean(),
  }),
});

const acceptedTermsSchema = z.object({ version: z.string(), accepted_at: instant });

const apiKeySchema = z.object({
  id: z.uuid(),
  public_id: z.string(),
  scopes: z.array(z.enum(SCOPES)),
  created_at: instant,
  expires_at: instant,
  last_used_at: instant.nullable(),
  revoked_at: instant.nullable(),
});

const apiKeyListSchema = z.object({ data: z.array(apiKeySchema) });

const createdApiKeySchema = z.object({
  id: z.uuid(),
  public_id: z.string(),
  token: z.string(),
  scopes: z.array(z.enum(SCOPES)),
  expires_at: instant,
});

/** 204 e o 202 do `DELETE /me` chegam sem corpo útil; só o status importa. */
const noContentSchema = z.unknown();

export type Me = z.infer<typeof meSchema>;
export type Scope = Me["scopes"][number];
export type ApiKey = z.infer<typeof apiKeySchema>;
export type CreatedApiKey = z.infer<typeof createdApiKeySchema>;
export type AcceptedTerms = z.infer<typeof acceptedTermsSchema>;
export type CreateApiKeyCommand =
  operations["createApiKey"]["requestBody"]["content"]["application/json"];

/** Chave do GET /me no cache: a sessão, os limites e o aceite dos termos saem daqui. */
export const ME_QUERY_KEY = ["me"] as const;
export const API_KEYS_QUERY_KEY = ["api-keys"] as const;

function withSignal(signal: AbortSignal | undefined): { signal?: AbortSignal } {
  return signal === undefined ? {} : { signal };
}

/** GET /me: a conta da credencial; sem sessão, 401 AUTHENTICATION_REQUIRED. */
export async function getMe(client: ApiClient, signal?: AbortSignal): Promise<Me> {
  const response = await client.request({
    method: "GET",
    path: "/api/v1/me",
    schema: meSchema,
    ...withSignal(signal),
  });
  return response.data;
}

/** POST /me/terms:accept com a versão que a pessoa leu; outra versão vigente → 409. */
export async function acceptTerms(client: ApiClient, version: string): Promise<AcceptedTerms> {
  const response = await client.request({
    method: "POST",
    path: "/api/v1/me/terms:accept",
    body: { version },
    schema: acceptedTermsSchema,
  });
  return response.data;
}

/** POST /auth/logout: encerra esta sessão; sem sessão válida também responde 204. */
export async function logout(client: ApiClient): Promise<void> {
  await client.request({ method: "POST", path: "/auth/logout", schema: noContentSchema });
}

/** POST /auth/logout-all: encerra todas as sessões e revoga todas as chaves da conta. */
export async function logoutAll(client: ApiClient): Promise<void> {
  await client.request({ method: "POST", path: "/auth/logout-all", schema: noContentSchema });
}

/** DELETE /me (202): pede a exclusão; exige login recente (REAUTH_REQUIRED). */
export async function deleteAccount(client: ApiClient): Promise<void> {
  await client.request({ method: "DELETE", path: "/api/v1/me", schema: noContentSchema });
}

/** GET /api-keys: metadados das chaves da conta, nunca o segredo. */
export async function listApiKeys(client: ApiClient, signal?: AbortSignal): Promise<ApiKey[]> {
  const response = await client.request({
    method: "GET",
    path: "/api/v1/api-keys",
    schema: apiKeyListSchema,
    ...withSignal(signal),
  });
  return response.data.data;
}

/** POST /api-keys: o segredo vem só nesta resposta; exige login recente (REAUTH_REQUIRED). */
export async function createApiKey(
  client: ApiClient,
  command: CreateApiKeyCommand,
): Promise<CreatedApiKey> {
  const response = await client.request({
    method: "POST",
    path: "/api/v1/api-keys",
    body: command,
    schema: createdApiKeySchema,
  });
  return response.data;
}

/** DELETE /api-keys/{id}: chave alheia ou inexistente → 404; revogar de novo → 204. */
export async function revokeApiKey(client: ApiClient, keyId: string): Promise<void> {
  await client.request({
    method: "DELETE",
    path: `/api/v1/api-keys/${encodeURIComponent(keyId)}`,
    schema: noContentSchema,
  });
}
