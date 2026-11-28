import { ApiError } from "../api/response";
import { SCOPES, type Scope } from "./api";
import { KEY_DAYS } from "./keys";

/**
 * Escolhas da chave nova na URL do Acesso, para atravessar o login de novo (REAUTH_REQUIRED): o
 * `SignInPrompt` leva a URL da tela no `return_to`, e na volta o formulário abre preenchido. Só
 * escopos e dias; o segredo da chave nunca passa por aqui.
 */
const NEW_KEY_PARAM = "nova_chave";
const SCOPE_PARAM = "escopo";
const DAYS_PARAM = "dias";

const DAYS_TEXT = /^\d{1,2}$/;

export interface NewKeyChoices {
  readonly scopes: readonly Scope[];
  readonly days: string;
}

/** Cópia de `current` com as escolhas no lugar das anteriores; escopos na ordem de `SCOPES`. */
export function newKeyChoicesToSearch(
  choices: NewKeyChoices,
  current: URLSearchParams,
): URLSearchParams {
  const next = withoutNewKeyChoices(current);
  next.set(NEW_KEY_PARAM, "1");
  const wanted = new Set(choices.scopes);
  for (const scope of SCOPES.filter((candidate) => wanted.has(candidate))) {
    next.append(SCOPE_PARAM, scope);
  }
  next.set(DAYS_PARAM, choices.days);
  return next;
}

/**
 * As escolhas da URL, ou `null` sem `nova_chave=1`. A URL vem de fora: escopo fora de `SCOPES` é
 * descartado, e dias fora de `KEY_DAYS` voltam ao inicial.
 */
export function newKeyChoicesFromSearch(search: URLSearchParams): NewKeyChoices | null {
  if (search.get(NEW_KEY_PARAM) !== "1") {
    return null;
  }
  const wanted = new Set(search.getAll(SCOPE_PARAM));
  return {
    scopes: SCOPES.filter((scope) => wanted.has(scope)),
    days: validDays(search.get(DAYS_PARAM)),
  };
}

/** Cópia de `search` sem os três parâmetros da chave nova. */
export function withoutNewKeyChoices(search: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(search);
  next.delete(NEW_KEY_PARAM);
  next.delete(SCOPE_PARAM);
  next.delete(DAYS_PARAM);
  return next;
}

/** O servidor pediu login recente para esta operação. */
export function isReauthRequired(error: Error): boolean {
  return (
    error instanceof ApiError &&
    error.failure.kind === "problem" &&
    error.failure.problem.code === "REAUTH_REQUIRED"
  );
}

function validDays(raw: string | null): string {
  if (raw === null || !DAYS_TEXT.test(raw)) {
    return String(KEY_DAYS.initial);
  }
  const days = Number(raw);
  // Só a forma que este módulo escreve: "07" também volta ao inicial.
  return days >= KEY_DAYS.min && days <= KEY_DAYS.max && String(days) === raw
    ? raw
    : String(KEY_DAYS.initial);
}
