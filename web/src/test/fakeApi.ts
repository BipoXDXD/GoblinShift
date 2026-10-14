import { vi, type Mock } from "vitest";
import { z } from "zod";

import { createApiClient, type ApiClient } from "../api/client";

/** Resposta de uma rota falsa; `null` deixa o pedido pendurado para sempre. */
type Handler = (request: Request) => Response | Promise<Response> | null;

/**
 * API falsa por `fetch`: cada rota é `"MÉTODO /caminho"` (sem query) e responde pelo handler.
 * Rota sem handler responde 404 problem+json, para o teste falhar alto em vez de esperar.
 */
export interface FakeApi {
  readonly client: ApiClient;
  readonly fetchMock: Mock<typeof fetch>;
  /** Cópia de cada pedido recebido, na ordem, com o corpo ainda legível. */
  readonly requests: readonly Request[];
}

export function fakeApi(handlers: Readonly<Record<string, Handler>>): FakeApi {
  const requests: Request[] = [];
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const request = new Request(input, init);
    requests.push(request.clone());
    const url = new URL(request.url);
    const handler = handlers[`${request.method} ${url.pathname}`];
    const response = handler === undefined ? problemResponse(404, "NOT_FOUND") : handler(request);
    return response === null ? new Promise<Response>(() => undefined) : Promise.resolve(response);
  });
  const client = createApiClient({ fetch: fetchMock, csrfToken: () => null, now: Date.now });
  return { client, fetchMock, requests };
}

export function jsonResponse(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

interface ProblemExtras {
  readonly errors?: readonly { field: string; code: string; message: string }[];
  readonly headers?: Record<string, string>;
}

export function problemResponse(
  status: number,
  code: string,
  extras: ProblemExtras = {},
): Response {
  return new Response(
    JSON.stringify({
      type: `https://goblinshift.dev/problems/${code.toLowerCase()}`,
      title: "Title from server",
      status,
      detail: "Detail from server",
      instance: "/api/v1/x",
      code,
      request_id: "req-1",
      ...(extras.errors === undefined ? {} : { errors: extras.errors }),
    }),
    {
      status,
      headers: { "Content-Type": "application/problem+json", ...extras.headers },
    },
  );
}

export const RUN_ID = "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a";

/** Execução no formato de GET /runs/{id}, com os campos a sobrescrever. */
export function runBody(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: RUN_ID,
    recipe_id: "csv-janitor",
    recipe_version: 1,
    status: "queued",
    attempt: 1,
    progress: { phase: null, processed: 0, total: 0 },
    queue_ahead: 0,
    created_at: "2026-10-14T12:00:00Z",
    queue_expires_at: "2026-10-14T13:00:00Z",
    deadline_at: null,
    error: null,
    ...overrides,
  };
}

const uploadItemsSchema = z.object({
  items: z.array(z.object({ name: z.string(), size: z.number() })),
});

/** POST /uploads falso: uma intenção por item, com ids previsíveis (…0001, …0002, ...). */
export function uploadIntentsHandler(): (request: Request) => Promise<Response> {
  let next = 0;
  return async (request: Request): Promise<Response> => {
    const { items } = uploadItemsSchema.parse(await request.json());
    return jsonResponse(
      {
        items: items.map(({ name, size }) => {
          next += 1;
          const id = uploadIdFor(next);
          return {
            id,
            name,
            size,
            state: "created",
            expires_at: "2026-10-15T12:00:00Z",
            content: {
              method: "PUT",
              url: `/api/v1/uploads/${id}/content`,
              headers: { "Content-Type": "application/octet-stream" },
            },
          };
        }),
      },
      201,
    );
  };
}

export function uploadIdFor(sequence: number): string {
  return `7d3c2a1e-0000-4000-8000-${String(sequence).padStart(12, "0")}`;
}
