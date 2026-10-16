import type { z } from "zod";

import { CSRF_HEADER, type CsrfTokenSource } from "./csrf";
import {
  ApiError,
  API_ACCEPT,
  errorFromResponse,
  interpretResponse,
  JSON_MEDIA_TYPE,
  type ApiResponse,
  type RawApiResponse,
} from "./response";
import { sameOriginUrl } from "./sameOrigin";

const DEFAULT_TIMEOUT_MS = 30_000;
const JPEG_ACCEPT = "image/jpeg, application/problem+json";
const JPEG_MEDIA_TYPE = "image/jpeg";
const JPEG_SIGNATURE = [0xff, 0xd8, 0xff] as const;
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

/** Imagem JPEG pequena derivada pelo próprio serviço, lida inteira para virar preview. */
export interface JpegRequest {
  /** Caminho na mesma origem, como o download de um artefato. */
  readonly path: string;
  /** Teto do corpo; acima dele a leitura é abortada. */
  readonly maxBytes: number;
  readonly signal?: AbortSignal;
}

export interface ApiClient {
  request<T>(request: ApiRequest<T>): Promise<ApiResponse<T>>;
  /**
   * O cookie legível de CSRF existe. Ele nasce e morre junto do cookie da sessão (HttpOnly, que a
   * página não lê): sem ele não há sessão a consultar, e o `GET /me` só daria 401 no console.
   */
  hasSessionCookie(): boolean;
  /**
   * GET de um JPEG com teto de bytes e conferência da assinatura (FF D8 FF); o Blob sai com tipo
   * `image/jpeg` fixo, nunca o que o servidor declarou. Falha é `ApiError`, com o Retry-After.
   */
  requestJpeg(request: JpegRequest): Promise<Blob>;
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
      const { response, body } = await sendWithTimeout(options, request, {
        accept: API_ACCEPT,
        read: readText,
        timeoutMs,
      });
      return interpretResponse(rawOf(response, body), request.schema, options.now());
    },
    async requestJpeg({ path, maxBytes, signal }: JpegRequest): Promise<Blob> {
      const outgoing = {
        method: "GET" as const,
        path,
        ...(signal === undefined ? {} : { signal }),
      };
      const { response, body } = await sendWithTimeout(options, outgoing, {
        accept: JPEG_ACCEPT,
        read: async (received) =>
          received.ok
            ? { ok: true as const, bytes: await readCapped(received, maxBytes) }
            : { ok: false as const, text: await received.text() },
        timeoutMs,
      });
      if (!body.ok) {
        throw errorFromResponse(rawOf(response, body.text), options.now());
      }
      if (body.bytes === null || !startsWithJpeg(body.bytes)) {
        throw new ApiError({
          kind: "unexpected-response",
          status: response.status,
          retryAfterMs: null,
        });
      }
      return new Blob([body.bytes], { type: JPEG_MEDIA_TYPE });
    },
    hasSessionCookie(): boolean {
      return options.csrfToken() !== null;
    },
  };
}

function rawOf(response: Response, text: string): RawApiResponse {
  return { status: response.status, header: (name) => response.headers.get(name), text };
}

function readText(response: Response): Promise<string> {
  return response.text();
}

/** Corpo inteiro até `maxBytes`; acima disso cancela a leitura e devolve `null`. */
async function readCapped(
  response: Response,
  maxBytes: number,
): Promise<Uint8Array<ArrayBuffer> | null> {
  const reader = response.body?.getReader();
  if (reader === undefined) {
    return new Uint8Array(0);
  }
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

function startsWithJpeg(bytes: Uint8Array): boolean {
  return JPEG_SIGNATURE.every((byte, index) => bytes[index] === byte);
}

type OutgoingRequest = Omit<ApiRequest<unknown>, "schema">;

/** Como pedir e ler o corpo: `Accept`, leitura (dentro do prazo) e o próprio prazo. */
interface Reception<B> {
  readonly accept: string;
  readonly read: (response: Response) => Promise<B>;
  readonly timeoutMs: number;
}

async function sendWithTimeout<B>(
  options: ApiClientOptions,
  request: OutgoingRequest,
  { accept, read, timeoutMs }: Reception<B>,
): Promise<{ response: Response; body: B }> {
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
      headers: requestHeaders(options, request, accept),
      body: request.body === undefined ? null : JSON.stringify(request.body),
      credentials: "same-origin",
      signal: controller.signal,
    });
    // O prazo cobre também a leitura do corpo, que pode parar no meio.
    return { response, body: await read(response) };
  } catch {
    // Cancelamento de quem chamou sai com o motivo original, para o TanStack Query reconhecê-lo.
    request.signal?.throwIfAborted();
    throw new ApiError(deadline.passed ? { kind: "timeout" } : { kind: "network" });
  } finally {
    clearTimeout(timer);
    request.signal?.removeEventListener("abort", forwardAbort);
  }
}

function requestHeaders(
  options: ApiClientOptions,
  request: OutgoingRequest,
  accept: string,
): Headers {
  const headers = new Headers({ Accept: accept });
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
