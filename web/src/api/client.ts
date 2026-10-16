import type { z } from "zod";

import { CSRF_HEADER, type CsrfTokenSource } from "./csrf";
import {
  ApiError,
  API_ACCEPT,
  interpretResponse,
  interpretTextResponse,
  JSON_MEDIA_TYPE,
  TEXT_ACCEPT,
  type ApiResponse,
  type RawApiResponse,
} from "./response";
import { sameOriginUrl } from "./sameOrigin";

const DEFAULT_TIMEOUT_MS = 30_000;
const SAFE_METHODS: ReadonlySet<HttpMethod> = new Set(["GET"]);

type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

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

/** GET de um corpo que não é JSON (a amostra CSV do preview), lido como texto UTF-8. */
export interface TextRequest {
  readonly path: string;
  readonly signal?: AbortSignal;
}

export interface ApiClient {
  request<T>(request: ApiRequest<T>): Promise<ApiResponse<T>>;
  /** Mesmo prazo, origem e tradução de falhas do `request`; o sucesso é o texto, sem parse. */
  requestText(request: TextRequest): Promise<ApiResponse<string>>;
  /**
   * O cookie legível de CSRF existe. Ele nasce e morre junto do cookie da sessão (HttpOnly, que a
   * página não lê): sem ele não há sessão a consultar, e o `GET /me` só daria 401 no console.
   */
  hasSessionCookie(): boolean;
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
 * Recusa caminho fora da origem (`CrossOriginUrlError`) antes de montar o pedido. Checa o status, faz o parse do problem+json, valida o corpo de sucesso pelo schema, põe o CSRF
 * nas mutações e corta a chamada no prazo. O cancelamento de quem chamou (`signal`) é propagado
 * como está, para o TanStack Query reconhecê-lo.
 */
export function createApiClient(options: ApiClientOptions): ApiClient {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  return {
    async request<T>(request: ApiRequest<T>): Promise<ApiResponse<T>> {
      const raw = await sendWithTimeout(options, request, timeoutMs);
      return interpretResponse(raw, request.schema, options.now());
    },
    async requestText(request: TextRequest): Promise<ApiResponse<string>> {
      const raw = await sendWithTimeout(
        options,
        { ...request, method: "GET", accept: TEXT_ACCEPT },
        timeoutMs,
      );
      return interpretTextResponse(raw, options.now());
    },
    hasSessionCookie(): boolean {
      return options.csrfToken() !== null;
    },
  };
}

/** O pedido como sai pela rede: o que `request` e `requestText` têm em comum. */
interface OutgoingRequest {
  readonly method: HttpMethod;
  readonly path: string;
  readonly body?: unknown;
  readonly idempotencyKey?: string;
  readonly signal?: AbortSignal;
  readonly accept?: string;
}

async function sendWithTimeout(
  options: ApiClientOptions,
  request: OutgoingRequest,
  timeoutMs: number,
): Promise<RawApiResponse> {
  request.signal?.throwIfAborted();
  const url = sameOriginUrl(request.path, window.location.origin);
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
    const response = await options.fetch(url, {
      method: request.method,
      headers: requestHeaders(options, request),
      body: request.body === undefined ? null : JSON.stringify(request.body),
      credentials: "same-origin",
      signal: controller.signal,
    });
    // O prazo cobre também a leitura do corpo, que pode parar no meio.
    const text = await response.text();
    return { status: response.status, header: (name) => response.headers.get(name), text };
  } catch {
    // Cancelamento de quem chamou sai com o motivo original, para o TanStack Query reconhecê-lo.
    request.signal?.throwIfAborted();
    throw new ApiError(deadline.passed ? { kind: "timeout" } : { kind: "network" });
  } finally {
    clearTimeout(timer);
    request.signal?.removeEventListener("abort", forwardAbort);
  }
}

function requestHeaders(options: ApiClientOptions, request: OutgoingRequest): Headers {
  const headers = new Headers({ Accept: request.accept ?? API_ACCEPT });
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
