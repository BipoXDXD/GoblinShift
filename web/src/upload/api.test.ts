import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { createApiClient } from "../api/client";
import type { components } from "../api/schema";
import {
  createUploads,
  deleteUpload,
  type ContentInstructions,
  type CreatedUpload,
  type ReceivedContent,
} from "./api";

type Schemas = components["schemas"];

const intent = {
  id: "7d3c2a1e-0000-4000-8000-000000000001",
  name: "clientes.csv",
  size: 4,
  state: "created",
  expires_at: "2026-10-15T12:00:00Z",
  content: {
    method: "PUT",
    url: "/api/v1/uploads/7d3c2a1e-0000-4000-8000-000000000001/content",
    headers: { "Content-Type": "application/octet-stream" },
  },
};

describe("createUploads", () => {
  it("cria as intenções com nome e tamanho e devolve-as validadas", async () => {
    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(
        new Response(JSON.stringify({ items: [intent] }), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const client = createApiClient({ fetch: fetchMock, csrfToken: () => null, now: Date.now });

    const created = await createUploads(client, [{ name: "clientes.csv", size: 4 }]);

    expect(created).toEqual([intent]);
    const [input, init] = fetchMock.mock.calls[0] ?? [];
    expect(input).toEqual(new URL("/api/v1/uploads", window.location.origin));
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ items: [{ name: "clientes.csv", size: 4 }] }));
  });
});

describe("tipos de upload espelham a spec", () => {
  it("ContentInstructions", () => {
    expectTypeOf<ContentInstructions>().toEqualTypeOf<Schemas["ContentInstructions"]>();
  });

  it("CreatedUpload", () => {
    expectTypeOf<CreatedUpload>().toEqualTypeOf<Schemas["CreatedUpload"]>();
  });

  it("ReceivedContent", () => {
    expectTypeOf<ReceivedContent>().toEqualTypeOf<Schemas["ReceivedContent"]>();
  });
});

describe("deleteUpload", () => {
  function clientAnswering(response: () => Response) {
    const fetchMock = vi.fn<typeof fetch>(() => Promise.resolve(response()));
    const client = createApiClient({ fetch: fetchMock, csrfToken: () => "csrf", now: Date.now });
    return { client, fetchMock };
  }

  function problemOf(status: number, code: string): Response {
    return new Response(
      JSON.stringify({
        type: "https://goblinshift.dev/problems/x",
        title: "t",
        status,
        detail: "d",
        instance: "/api/v1/uploads/x",
        code,
        request_id: "r",
      }),
      { status, headers: { "Content-Type": "application/problem+json" } },
    );
  }

  it("manda o DELETE do upload com o CSRF e aceita o 202", async () => {
    const { client, fetchMock } = clientAnswering(() => new Response(null, { status: 202 }));

    await deleteUpload(client, intent.id);

    const request = new Request(...(fetchMock.mock.calls[0] ?? ["http://x"]));
    expect(request.method).toBe("DELETE");
    expect(new URL(request.url).pathname).toBe(`/api/v1/uploads/${intent.id}`);
    expect(request.headers.get("X-CSRF-Token")).toBe("csrf");
  });

  it.each([
    [404, "NOT_FOUND"],
    [410, "GONE"],
  ])("upload que já não existe (%i) conta como excluído", async (status, code) => {
    const { client } = clientAnswering(() => problemOf(status, code));

    await expect(deleteUpload(client, intent.id)).resolves.toBeUndefined();
  });

  it("outra falha chega a quem chamou", async () => {
    const { client } = clientAnswering(() => problemOf(500, "INTERNAL_ERROR"));

    await expect(deleteUpload(client, intent.id)).rejects.toMatchObject({
      failure: { kind: "problem", status: 500 },
    });
  });
});
