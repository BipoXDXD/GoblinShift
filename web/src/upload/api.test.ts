import { describe, expect, expectTypeOf, it, vi } from "vitest";

import { createApiClient } from "../api/client";
import type { components } from "../api/schema";
import {
  createUploads,
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
