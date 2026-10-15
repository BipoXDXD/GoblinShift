import { TEXT } from "../text";
import type { ApiKey, CreateApiKeyCommand, Scope } from "./api";

const T = TEXT.account.keys;

// Faixa da validade de identity/keys.py (MIN_KEY_DAYS, DEFAULT_KEY_DAYS, MAX_KEY_DAYS): a spec só
// publica o default. O servidor confere de novo e responde 422 fora dela.
export const KEY_DAYS = { min: 1, initial: 30, max: 90 } as const;
const WHOLE_NUMBER = /^\d{1,3}$/;

export type KeyStatus = "active" | "expired" | "revoked";

/** Estado de uma chave no instante `nowMs`: revogada vence expirada (as duas não autenticam). */
export function keyStatus(key: ApiKey, nowMs: number): KeyStatus {
  if (key.revoked_at !== null) {
    return "revoked";
  }
  return Date.parse(key.expires_at) <= nowMs ? "expired" : "active";
}

/** O que a pessoa marcou e digitou no formulário de chave nova. */
export interface NewKeyDraft {
  readonly scopes: ReadonlySet<Scope>;
  readonly days: string;
}

export interface NewKeyErrors {
  readonly scopes: string | null;
  readonly days: string | null;
}

export type ParsedNewKey =
  | { readonly ok: true; readonly command: CreateApiKeyCommand }
  | { readonly ok: false; readonly errors: NewKeyErrors };

/** Rascunho → corpo do POST /api-keys, ou os erros de cada campo. */
export function parseNewKey(draft: NewKeyDraft, scopeOrder: readonly Scope[]): ParsedNewKey {
  const scopes = scopeOrder.filter((scope) => draft.scopes.has(scope));
  const days = parseDays(draft.days.trim());
  const errors: NewKeyErrors = {
    scopes: scopes.length === 0 ? T.scopesRequired : null,
    days: days === null ? T.daysInvalid(KEY_DAYS.min, KEY_DAYS.max) : null,
  };
  if (days === null || errors.scopes !== null) {
    return { ok: false, errors };
  }
  return { ok: true, command: { scopes, days } };
}

function parseDays(value: string): number | null {
  if (!WHOLE_NUMBER.test(value)) {
    return null;
  }
  const days = Number(value);
  return days >= KEY_DAYS.min && days <= KEY_DAYS.max ? days : null;
}
