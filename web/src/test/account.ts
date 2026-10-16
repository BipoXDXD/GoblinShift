import type { components } from "../api/schema";

type Schemas = components["schemas"];

const ACCOUNT_ID = "5f0c7c1a-2b3d-4e5f-8a9b-0c1d2e3f4a5b";
export const KEY_ID = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d";
export const SECOND_KEY_ID = "1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e";

interface MeOverrides {
  readonly via?: Schemas["MeResponse"]["via"];
  readonly usage?: Partial<Schemas["Usage"]>;
  readonly limits?: Partial<Schemas["Limits"]>;
  readonly terms?: Partial<Schemas["Terms"]>;
}

/** Corpo de GET /me de uma conta com sessão, termos aceitos e cotas com folga. */
export function meBody(overrides: MeOverrides = {}): Schemas["MeResponse"] {
  return {
    id: ACCOUNT_ID,
    display_name: "Ana",
    scopes: ["files:read", "files:write", "runs:read", "runs:write"],
    via: overrides.via ?? "session",
    limits: {
      runs_per_24h: 20,
      input_bytes_per_24h: 2_000_000_000,
      storage_bytes: 5_000_000_000,
      active_runs: 1,
      file_bytes: 268_435_456,
      batch_files: 50,
      batch_bytes: 536_870_912,
      concurrent_uploads: 4,
      concurrent_downloads: 4,
      ...overrides.limits,
    },
    usage: {
      runs_last_24h: 3,
      input_bytes_last_24h: 12_500_000,
      storage_reserved_bytes: 40_000_000,
      active_run_id: null,
      daily_quota_resets_at: "2026-10-15T09:30:00Z",
      executor_ms_last_24h: 95_000,
      ...overrides.usage,
    },
    terms: {
      current_version: "v1",
      accepted_version: "v1",
      accepted_at: "2026-10-01T12:00:00Z",
      acceptance_required: false,
      ...overrides.terms,
    },
  };
}

/** Termos vigentes ainda não aceitos: as rotas fora da allowlist respondem 403. */
export const TERMS_PENDING = {
  accepted_version: null,
  accepted_at: null,
  acceptance_required: true,
} as const;

/** Chave ativa no formato de GET /api-keys. */
export function apiKeyBody(
  overrides: Partial<Schemas["ApiKeyResponse"]> = {},
): Schemas["ApiKeyResponse"] {
  return {
    id: KEY_ID,
    public_id: "gsk_7Hq2",
    scopes: ["runs:read", "runs:write"],
    created_at: "2026-10-01T12:00:00Z",
    expires_at: "2026-10-31T12:00:00Z",
    last_used_at: null,
    revoked_at: null,
    ...overrides,
  };
}
