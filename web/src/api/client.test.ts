import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { createApiClient } from "./client";
import { ApiError, type ApiFailure } from "./response";
import { CrossOriginUrlError } from "./sameOrigin";

const NOW_MS = Date.parse("2026-10-14T12:00:00Z");
const thing = z.object({ id: z.string() });
const rateLimited = {
  type: "https://goblinshift.dev/problems/rate-limited",
  title: "Muitas requisições",
  status: 429,
  detail: "Tente de novo depois do Retry-After.",
  instance: "/api/v1/coisas",
  code: "RATE_LIMITED",
  request_id: "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a",
};

function jsonResponse(
  body: unknown,
  { status = 200, headers = {} }: { status?: number; headers?: Record<string, string> } = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function problemResponse(body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 429,
    headers: { "Content-Type": "application/problem+json", ...headers },
  });
}

function setup(response: Response | Error, csrfToken: string | null = null) {
  const fetchMock = vi.fn<typeof fetch>(() =>
    response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
  );
  const client = createApiClient({
    fetch: fetchMock,
    csrfToken: () => csrfToken,
    now: () => NOW_MS,
  });
  return { client, fetchMock };
}

function sentRequest(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>): Request {
  const call = fetchMock.mock.calls[0];
  if (call === undefined) {
    throw new Error("fetch não foi chamado");
  }
  return new Request(...call);
}

async function failureOf(promise: Promise<unknown>): Promise<ApiFailure> {
  const error: unknown = await promise.then(
    () => null,
    (reason: unknown) => reason,
  );
  if (!(error instanceof ApiError)) {
    throw new Error(`esperava ApiError, veio ${String(error)}`);
  }
  return error.failure;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("createApiClient: sucesso", () => {
  it("devolve o corpo já validado pelo schema, com status", async () => {
    const { client } = setup(jsonResponse({ id: "a", extra: 1 }));

    const response = await client.request({
      method: "GET",
      path: "/api/v1/coisas/a",
      schema: thing,
    });

    expect(response).toEqual({ data: { id: "a" }, status: 200, retryAfterMs: null });
  });

  it("expõe o Retry-After de uma resposta de sucesso (polling)", async () => {
    const { client } = setup(jsonResponse({ id: "a" }, { headers: { "Retry-After": "2" } }));

    const response = await client.request({
      method: "GET",
      path: "/api/v1/coisas/a",
      schema: thing,
    });

    expect(response.retryAfterMs).toBe(2_000);
  });

  it("aceita resposta sem corpo quando o schema espera nada", async () => {
    const { client } = setup(new Response(null, { status: 202 }));

    const response = await client.request({
      method: "DELETE",
      path: "/api/v1/coisas/a",
      schema: z.undefined(),
    });

    expect(response).toEqual({ data: undefined, status: 202, retryAfterMs: null });
  });
});

describe("createApiClient: cabeçalhos", () => {
  it("em GET pede JSON e não manda CSRF, corpo nem Content-Type", async () => {
    const { client, fetchMock } = setup(jsonResponse({ id: "a" }), "tok");

    await client.request({ method: "GET", path: "/api/v1/coisas/a", schema: thing });

    const request = sentRequest(fetchMock);
    expect(request.method).toBe("GET");
    expect(new URL(request.url).pathname).toBe("/api/v1/coisas/a");
    expect(request.headers.get("Accept")).toBe("application/json, application/problem+json");
    expect(request.headers.has("X-CSRF-Token")).toBe(false);
    expect(request.headers.has("Content-Type")).toBe(false);
  });

  it("em mutação manda JSON, CSRF e Idempotency-Key", async () => {
    const { client, fetchMock } = setup(jsonResponse({ id: "a" }, { status: 201 }), "tok");

    await client.request({
      method: "POST",
      path: "/api/v1/coisas",
      body: { nome: "x" },
      idempotencyKey: "chave-de-intencao-0001",
      schema: thing,
    });

    const request = sentRequest(fetchMock);
    expect(request.headers.get("Content-Type")).toBe("application/json");
    expect(request.headers.get("X-CSRF-Token")).toBe("tok");
    expect(request.headers.get("Idempotency-Key")).toBe("chave-de-intencao-0001");
    expect(await request.json()).toEqual({ nome: "x" });
  });

  it("em mutação sem token de CSRF não inventa o header", async () => {
    const { client, fetchMock } = setup(jsonResponse({ id: "a" }), null);

    await client.request({ method: "DELETE", path: "/api/v1/coisas/a", schema: thing });

    expect(sentRequest(fetchMock).headers.has("X-CSRF-Token")).toBe(false);
  });

  it("não manda Idempotency-Key quando não há intenção", async () => {
    const { client, fetchMock } = setup(jsonResponse({ id: "a" }));

    await client.request({ method: "POST", path: "/api/v1/coisas", body: {}, schema: thing });

    expect(sentRequest(fetchMock).headers.has("Idempotency-Key")).toBe(false);
  });
});

describe("createApiClient: origem", () => {
  it.each(["https://evil.example/api/v1/coisas", "//evil.example/api/v1/coisas"])(
    "recusa caminho fora da origem (%s) sem chamar a API nem expor o CSRF",
    async (path) => {
      const { client, fetchMock } = setup(jsonResponse({ id: "1" }), "tok");

      await expect(
        client.request({ method: "POST", path, body: {}, schema: thing }),
      ).rejects.toBeInstanceOf(CrossOriginUrlError);
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );
});

describe("createApiClient: falhas", () => {
  it("traduz problem+json em ApiError com código e Retry-After", async () => {
    const { client } = setup(problemResponse(rateLimited, { "Retry-After": "5" }));

    const failure = await failureOf(
      client.request({ method: "GET", path: "/api/v1/coisas", schema: thing }),
    );

    expect(failure).toEqual({
      kind: "problem",
      status: 429,
      problem: rateLimited,
      retryAfterMs: 5_000,
    });
  });

  it("trata erro sem problem+json (ex.: 502 do proxy) como resposta inesperada", async () => {
    const { client } = setup(
      new Response("<html>Bad Gateway</html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );

    const failure = await failureOf(
      client.request({ method: "GET", path: "/api/v1/coisas", schema: thing }),
    );

    expect(failure).toEqual({ kind: "unexpected-response", status: 502, retryAfterMs: null });
  });

  it("trata problem+json fora do catálogo como resposta inesperada", async () => {
    const { client } = setup(
      problemResponse({ ...rateLimited, code: "DESCONHECIDO" }, { "Retry-After": "1" }),
    );

    const failure = await failureOf(
      client.request({ method: "GET", path: "/api/v1/coisas", schema: thing }),
    );

    expect(failure).toEqual({ kind: "unexpected-response", status: 429, retryAfterMs: 1_000 });
  });

  it("recusa corpo de sucesso que não bate com o schema", async () => {
    const { client } = setup(jsonResponse({ id: 1 }));

    const failure = await failureOf(
      client.request({ method: "GET", path: "/api/v1/coisas/a", schema: thing }),
    );

    expect(failure).toEqual({ kind: "unexpected-response", status: 200, retryAfterMs: null });
  });

  it("recusa corpo de sucesso que não é JSON", async () => {
    const { client } = setup(new Response("{quebrado", { status: 200 }));

    const failure = await failureOf(
      client.request({ method: "GET", path: "/api/v1/coisas/a", schema: thing }),
    );

    expect(failure.kind).toBe("unexpected-response");
  });

  it("traduz falha de rede", async () => {
    const { client } = setup(new TypeError("Failed to fetch"));

    const failure = await failureOf(
      client.request({ method: "GET", path: "/api/v1/coisas", schema: thing }),
    );

    expect(failure).toEqual({ kind: "network" });
  });
});

describe("createApiClient: prazo e cancelamento", () => {
  function hangingFetch() {
    return vi.fn<typeof fetch>(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("cancelada", "AbortError"));
          });
        }),
    );
  }

  it("desiste depois do prazo com falha de timeout", async () => {
    vi.useFakeTimers();
    const client = createApiClient({
      fetch: hangingFetch(),
      csrfToken: () => null,
      now: () => NOW_MS,
      timeoutMs: 1_000,
    });

    const pending = failureOf(client.request({ method: "GET", path: "/x", schema: thing }));
    await vi.advanceTimersByTimeAsync(1_000);

    expect(await pending).toEqual({ kind: "timeout" });
  });

  it("não chama a API com o sinal já cancelado", async () => {
    const fetchMock = hangingFetch();
    const client = createApiClient({ fetch: fetchMock, csrfToken: () => null, now: () => NOW_MS });

    const pending = client.request({
      method: "GET",
      path: "/x",
      schema: thing,
      signal: AbortSignal.abort(),
    });

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("aplica o prazo também à leitura do corpo", async () => {
    vi.useFakeTimers();
    const client = createApiClient({
      // Como o fetch real: o corpo chega aos poucos e o abort do sinal interrompe a leitura.
      fetch: (_input, init) => {
        const stalledBody = new ReadableStream<Uint8Array>({
          start(controller) {
            init?.signal?.addEventListener("abort", () => {
              controller.error(new DOMException("abortada", "AbortError"));
            });
          },
        });
        return Promise.resolve(new Response(stalledBody, { status: 200 }));
      },
      csrfToken: () => null,
      now: () => NOW_MS,
      timeoutMs: 1_000,
    });

    const pending = failureOf(client.request({ method: "GET", path: "/x", schema: thing }));
    await vi.advanceTimersByTimeAsync(1_000);

    expect(await pending).toEqual({ kind: "timeout" });
  });

  it("propaga o cancelamento de quem chamou sem virar ApiError", async () => {
    const client = createApiClient({
      fetch: hangingFetch(),
      csrfToken: () => null,
      now: () => NOW_MS,
    });
    const controller = new AbortController();

    const pending = client.request({
      method: "GET",
      path: "/x",
      schema: thing,
      signal: controller.signal,
    });
    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("createApiClient: requestJpeg", () => {
  const PATH = "/api/v1/artifacts/a/download";
  const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10, 0xff, 0xd9]);

  function bytesResponse(
    bytes: Uint8Array<ArrayBuffer>,
    contentType = "application/octet-stream",
  ): Response {
    return new Response(bytes, { status: 200, headers: { "Content-Type": contentType } });
  }

  it("devolve o JPEG como Blob image/jpeg, mesmo servido como octet-stream", async () => {
    const { client, fetchMock } = setup(bytesResponse(JPEG));

    const blob = await client.requestJpeg({ path: PATH, maxBytes: 1024 });

    expect(blob.type).toBe("image/jpeg");
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(JPEG);
    const sent = sentRequest(fetchMock);
    expect(sent.method).toBe("GET");
    expect(sent.headers.get("Accept")).toBe("image/jpeg, application/problem+json");
  });

  it("aceita o corpo exatamente no teto", async () => {
    const { client } = setup(bytesResponse(JPEG));

    await expect(
      client.requestJpeg({ path: PATH, maxBytes: JPEG.byteLength }),
    ).resolves.toBeInstanceOf(Blob);
  });

  it("acima do teto aborta como resposta inesperada", async () => {
    const { client } = setup(bytesResponse(JPEG));

    const failure = await failureOf(
      client.requestJpeg({ path: PATH, maxBytes: JPEG.byteLength - 1 }),
    );

    expect(failure).toEqual({ kind: "unexpected-response", status: 200, retryAfterMs: null });
  });

  it.each([
    ["HTML", new TextEncoder().encode("<svg onload=alert(1)>")],
    ["vazio", new Uint8Array(0)],
    ["PNG", new Uint8Array([0x89, 0x50, 0x4e, 0x47])],
  ])("corpo que não começa com FF D8 FF (%s) é recusado", async (_, bytes) => {
    const { client } = setup(bytesResponse(bytes, "image/jpeg"));

    const failure = await failureOf(client.requestJpeg({ path: PATH, maxBytes: 1024 }));

    expect(failure.kind).toBe("unexpected-response");
  });

  it("429 vira ApiError com o Retry-After", async () => {
    const { client } = setup(problemResponse(rateLimited, { "Retry-After": "3" }));

    const failure = await failureOf(client.requestJpeg({ path: PATH, maxBytes: 1024 }));

    expect(failure).toEqual({
      kind: "problem",
      status: 429,
      problem: rateLimited,
      retryAfterMs: 3_000,
    });
  });

  it("recusa caminho de outra origem antes de pedir", async () => {
    const { client, fetchMock } = setup(bytesResponse(JPEG));

    await expect(
      client.requestJpeg({ path: "https://evil.example/x.jpg", maxBytes: 1024 }),
    ).rejects.toBeInstanceOf(CrossOriginUrlError);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
