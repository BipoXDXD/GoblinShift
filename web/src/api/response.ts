import type { z } from "zod";

import { parseProblem, type ApiProblem } from "./problem";
import { parseRetryAfterMs } from "./retryAfter";

export const JSON_MEDIA_TYPE = "application/json";
const PROBLEM_MEDIA_TYPE = "application/problem+json";
export const API_ACCEPT = `${JSON_MEDIA_TYPE}, ${PROBLEM_MEDIA_TYPE}`;
/** Corpo opaco (amostra do preview) no sucesso; a falha continua problem+json. */
export const TEXT_ACCEPT = `application/octet-stream, ${PROBLEM_MEDIA_TYPE}`;

/** Por que uma chamada à API falhou; `problem` traz o problem+json do catálogo. */
export type ApiFailure =
  | { kind: "problem"; status: number; problem: ApiProblem; retryAfterMs: number | null }
  | { kind: "unexpected-response"; status: number; retryAfterMs: number | null }
  | { kind: "network" }
  | { kind: "timeout" };

export class ApiError extends Error {
  readonly failure: ApiFailure;

  constructor(failure: ApiFailure) {
    super(`chamada à API falhou: ${failure.kind}`);
    this.name = "ApiError";
    this.failure = failure;
  }
}

export interface ApiResponse<T> {
  readonly data: T;
  readonly status: number;
  /** Espera pedida pelo servidor antes da próxima consulta (polling), em ms. */
  readonly retryAfterMs: number | null;
}

/** Resposta já recebida, venha de `fetch` ou de `XMLHttpRequest`. */
export interface RawApiResponse {
  readonly status: number;
  readonly header: (name: string) => string | null;
  readonly text: string;
}

const OK_MIN = 200;
const OK_MAX = 299;

/**
 * Interpreta a resposta da API: corpo de sucesso validado pelo schema, ou `ApiError` com o
 * problem+json do catálogo e o Retry-After.
 */
export function interpretResponse<T>(
  raw: RawApiResponse,
  schema: z.ZodType<T>,
  nowMs: number,
): ApiResponse<T> {
  const retryAfterMs = parseRetryAfterMs(raw.header("Retry-After"), nowMs);
  const body = parseJsonBody(raw.text);
  if (!isSuccess(raw)) {
    throw new ApiError(failureFrom(raw, body, retryAfterMs));
  }
  const parsed = body.ok ? schema.safeParse(body.value) : null;
  if (parsed?.success !== true) {
    throw new ApiError({ kind: "unexpected-response", status: raw.status, retryAfterMs });
  }
  return { data: parsed.data, status: raw.status, retryAfterMs };
}

/** `ApiError` de uma resposta fora de 2xx, com o problem+json do catálogo e o Retry-After. */
function errorFromResponse(raw: RawApiResponse, nowMs: number): ApiError {
  const retryAfterMs = parseRetryAfterMs(raw.header("Retry-After"), nowMs);
  return new ApiError(failureFrom(raw, parseJsonBody(raw.text), retryAfterMs));
}

/** Como `interpretResponse`, mas o corpo de sucesso é o texto como veio, sem parse. */
export function interpretTextResponse(raw: RawApiResponse, nowMs: number): ApiResponse<string> {
  if (!isSuccess(raw)) {
    throw errorFromResponse(raw, nowMs);
  }
  const retryAfterMs = parseRetryAfterMs(raw.header("Retry-After"), nowMs);
  return { data: raw.text, status: raw.status, retryAfterMs };
}

function isSuccess(raw: RawApiResponse): boolean {
  return raw.status >= OK_MIN && raw.status <= OK_MAX;
}

type JsonBody = { ok: true; value: unknown } | { ok: false };

function parseJsonBody(text: string): JsonBody {
  if (text === "") {
    return { ok: true, value: undefined };
  }
  try {
    const value: unknown = JSON.parse(text);
    return { ok: true, value };
  } catch {
    // Corpo que não é JSON (página de erro do proxy, resposta truncada): quem decide é o status.
    return { ok: false };
  }
}

function failureFrom(raw: RawApiResponse, body: JsonBody, retryAfterMs: number | null): ApiFailure {
  const isProblem = raw.header("Content-Type")?.startsWith(PROBLEM_MEDIA_TYPE) === true;
  const problem = isProblem && body.ok ? parseProblem(body.value) : null;
  if (problem === null) {
    return { kind: "unexpected-response", status: raw.status, retryAfterMs };
  }
  return { kind: "problem", status: raw.status, problem, retryAfterMs };
}
