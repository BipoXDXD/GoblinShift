import { describe, expect, expectTypeOf, it } from "vitest";

import type { components } from "../api/schema";
import type { RunCommand } from "../api/types";
import { fakeApi, jsonResponse, runBody, RUN_ID } from "../test/fakeApi";
import {
  artifactDownloadPath,
  cancelRun,
  type CancelOutcome,
  createRun,
  getRunResults,
  type AcceptedRun,
  type RunResults,
} from "./commands";

const command: RunCommand = {
  recipe_id: "csv-janitor",
  recipe_version: 1,
  parameters: {},
  input_upload_ids: ["7d3c2a1e-0000-4000-8000-000000000001"],
};

describe("tipos espelham a spec", () => {
  it("AcceptedRun", () => {
    expectTypeOf<AcceptedRun>().toEqualTypeOf<components["schemas"]["AcceptedRunResponse"]>();
  });

  it("CancelOutcome", () => {
    expectTypeOf<CancelOutcome>().toEqualTypeOf<components["schemas"]["CancelResponse"]>();
  });

  it("RunResults", () => {
    expectTypeOf<RunResults>().toEqualTypeOf<components["schemas"]["RunResultsResponse"]>();
  });
});

describe("createRun", () => {
  it("manda o comando com a Idempotency-Key e devolve a execução aceita", async () => {
    const accepted = { ...runBody(), idempotency_expires_at: "2026-10-15T12:00:00Z" };
    const { client, requests } = fakeApi({
      "POST /api/v1/runs": () => jsonResponse(accepted, 202),
    });

    const run = await createRun(client, { command, idempotencyKey: "chave-de-intencao-123" });

    expect(run.id).toBe(RUN_ID);
    const [request] = requests;
    expect(request?.headers.get("Idempotency-Key")).toBe("chave-de-intencao-123");
    expect(await request?.json()).toEqual(command);
  });
});

describe("cancelRun", () => {
  it("manda o POST de cancelamento e devolve o estado que o servidor decidiu", async () => {
    const { client, requests } = fakeApi({
      [`POST /api/v1/runs/${RUN_ID}/cancel`]: () =>
        jsonResponse({ id: RUN_ID, status: "stopping", error_code: null }, 202),
    });

    const result = await cancelRun(client, RUN_ID);

    expect(result).toEqual({
      outcome: { id: RUN_ID, status: "stopping", error_code: null },
      accepted: true,
    });
    expect(requests.map((request) => request.method)).toEqual(["POST"]);
  });

  it("execução já terminal (200) não conta como pedido aceito", async () => {
    const { client } = fakeApi({
      [`POST /api/v1/runs/${RUN_ID}/cancel`]: () =>
        jsonResponse({ id: RUN_ID, status: "succeeded", error_code: null }),
    });

    expect((await cancelRun(client, RUN_ID)).accepted).toBe(false);
  });
});

describe("getRunResults", () => {
  it("lê os artefatos publicados", async () => {
    const results = {
      published: true,
      expires_at: "2026-10-21T12:00:00Z",
      artifacts: [
        {
          artifact_id: "7d3c2a1e-0000-4000-8000-0000000000aa",
          logical_name: "clientes",
          role: "output",
          file_name: "clientes.csv",
          availability: "available",
          source_artifact_id: null,
        },
      ],
    };
    const { client } = fakeApi({
      [`GET /api/v1/runs/${RUN_ID}/results`]: () => jsonResponse(results),
    });

    expect(await getRunResults(client, RUN_ID)).toEqual(results);
  });
});

describe("artifactDownloadPath", () => {
  it("aponta a rota de download, com o id escapado", () => {
    expect(artifactDownloadPath("a/b")).toBe("/api/v1/artifacts/a%2Fb/download");
  });
});
