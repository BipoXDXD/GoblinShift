import type { z } from "zod";

import { CSRF_HEADER, type CsrfTokenSource } from "./csrf";
import { parseProblem, type ApiProblem } from "./problem";
import { parseRetryAfterMs } from "./retryAfter";

const DEFAULT_TIMEOUT_MS = 30_000;
const JSON_MEDIA_TYPE = "application/json";
const PROBLEM_MEDIA_TYPE = "application/problem+json";
const SAFE_METHODS: ReadonlySet<HttpMethod> = new Set(["GET"]);

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

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

export interface ApiRequest<T> {
  readonly method: HttpMethod;
  /** Caminho na mesma origem, por exemplo `/api/v1/runs/{id}`. */
  readonly path: string;
  /** Corpo JSON das mutações. */
  readonly body?: unknown;
  /** Chave da intenção (useIdempotencyKey) nos POST que a API deduplica. */
  readonly idempotencyKey?: string;
  readonly signal?: AbortSignal;
  /** Schema do corpo de sucesso: a resposta só chega a quem chamou depois do parse. */
  readonly schema: z.ZodType<T>;
}

export interface ApiResponse<T> {
  readonly data: T;
  readonly status: number;
  /** Espera pedida pelo servidor antes da próxima consulta (polling), em ms. */
  readonly retryAfterMs: number | null;
}

export interface ApiClient {
  request<T>(request: ApiRequest<T>): Promise<ApiResponse<T>>;
}

export interface ApiClientOptions {
  /** No navegador, `(input, init) => window.fetch(input, init)`: o fetch exige `this` = window. */
  readonly fetch: typeof fetch;
  readonly csrfToken: CsrfTokenSource;
  readonly now: () => number;
  readonly timeoutMs?: number;
}

/**
 * O único ponto da interface que fala com a API por `fetch` (TS 3).
 *
 * Checa o status, faz o parse do problem+json, valida o corpo de sucesso pelo schema, põe o CSRF
 * nas mutações e corta a chamada no prazo. O cancelamento de quem chamou (`signal`) é propagado
 * como está, para o TanStack Query reconhecê-lo.
 */
export function createApiClient(options: ApiClientOptions): ApiClient {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  return {
    async request<T>(request: ApiRequest<T>): Promise<ApiResponse<T>> {
      const { response, text } = await sendWithTimeout(options, request, timeoutMs);
      const retryAfterMs = parseRetryAfterMs(response.headers.get("Retry-After"), options.now());
      const raw = parseJsonBody(text);
      if (!response.ok) {
        throw new ApiError(failureFrom(response, raw, retryAfterMs));
      }
      const parsed = raw.ok ? request.schema.safeParse(raw.value) : null;
      if (parsed?.success !== true) {
        throw new ApiError({ kind: "unexpected-response", status: response.status, retryAfterMs });
      }
      return { data: parsed.data, status: response.status, retryAfterMs };
    },
  };
}

async function sendWithTimeout(
  options: ApiClientOptions,
  request: ApiRequest<unknown>,
  timeoutMs: number,
): Promise<{ response: Response; text: string }> {
  request.signal?.throwIfAborted();
  const controller = new AbortController();
  const deadline = { passed: false };
  const timer = setTimeout(() => {
    deadline.passed = true;
    controller.abort();
  }, timeoutMs);
  const forwardAbort = (): void => {
    controller.abort();
  };
  request.signal?.addEventListener("abort", forwardAbort, { once: true });
  try {
    const response = await options.fetch(new URL(request.path, window.location.origin), {
      method: request.method,
      headers: requestHeaders(options, request),
      body: request.body === undefined ? null : JSON.stringify(request.body),
      credentials: "same-origin",
      signal: controller.signal,
    });
    // O prazo cobre também a leitura do corpo, que pode parar no meio.
    return { response, text: await response.text() };
  } catch {
    // Cancelamento de quem chamou sai com o motivo original, para o TanStack Query reconhecê-lo.
    request.signal?.throwIfAborted();
    throw new ApiError(deadline.passed ? { kind: "timeout" } : { kind: "network" });
  } finally {
    clearTimeout(timer);
    request.signal?.removeEventListener("abort", forwardAbort);
  }
}

function requestHeaders(options: ApiClientOptions, request: ApiRequest<unknown>): Headers {
  const headers = new Headers({ Accept: `${JSON_MEDIA_TYPE}, ${PROBLEM_MEDIA_TYPE}` });
  if (request.body !== undefined) {
    headers.set("Content-Type", JSON_MEDIA_TYPE);
  }
  if (request.idempotencyKey !== undefined) {
    headers.set("Idempotency-Key", request.idempotencyKey);
  }
  if (!SAFE_METHODS.has(request.method)) {
    const token = options.csrfToken();
    if (token !== null) {
      headers.set(CSRF_HEADER, token);
    }
  }
  return headers;
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

function failureFrom(response: Response, body: JsonBody, retryAfterMs: number | null): ApiFailure {
  const isProblem = response.headers.get("Content-Type")?.startsWith(PROBLEM_MEDIA_TYPE) === true;
  const problem = isProblem && body.ok ? parseProblem(body.value) : null;
  if (problem === null) {
    return { kind: "unexpected-response", status: response.status, retryAfterMs };
  }
  return { kind: "problem", status: response.status, problem, retryAfterMs };
}
