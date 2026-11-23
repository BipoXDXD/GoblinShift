import { describe, expect, expectTypeOf, it } from "vitest";

import { ApiError } from "../api/response";
import type { components, operations } from "../api/schema";
import { fakeApi, jsonResponse, problemResponse } from "../test/fakeApi";
import { presetBody, PRESET_ID } from "../test/presets";
import {
  createPreset,
  deletePreset,
  getPreset,
  listPresets,
  replacePreset,
  type Preset,
  type PresetDraft,
  type PresetReplacement,
} from "./api";

describe("tipos espelham a spec", () => {
  it("Preset", () => {
    expectTypeOf<Preset>().toEqualTypeOf<components["schemas"]["PresetResponse"]>();
  });

  it("PresetDraft é o corpo de createPreset", () => {
    expectTypeOf<PresetDraft>().toEqualTypeOf<
      operations["createPreset"]["requestBody"]["content"]["application/json"]
    >();
  });
});

describe("tipos de replacePreset", () => {
  it("o corpo do PUT é o mesmo do POST, e a Bancada pode mandá-lo", () => {
    expectTypeOf<PresetDraft>().toEqualTypeOf<
      operations["replacePreset"]["requestBody"]["content"]["application/json"]
    >();
    expectTypeOf<PresetDraft>().toExtend<PresetReplacement>();
  });
});

describe("listPresets", () => {
  it("lê a lista do envelope data", async () => {
    const { client } = fakeApi({
      "GET /api/v1/presets": () => jsonResponse({ data: [presetBody()] }),
    });

    expect(await listPresets(client)).toEqual([presetBody()]);
  });
});

describe("getPreset", () => {
  it("lê um preset pelo id, escapado", async () => {
    const { client, requests } = fakeApi({
      [`GET /api/v1/presets/${PRESET_ID}`]: () => jsonResponse(presetBody()),
    });

    expect((await getPreset(client, PRESET_ID)).name).toBe("Clientes do mês");
    expect(new URL(requests[0]?.url ?? "").pathname).toBe(`/api/v1/presets/${PRESET_ID}`);
  });
});

describe("createPreset", () => {
  it("manda nome, receita, versão e parâmetros e devolve o preset criado", async () => {
    const draft: PresetDraft = {
      name: "Clientes do mês",
      recipe_id: "csv-janitor",
      recipe_version: 1,
      parameters: { output_delimiter: ";" },
    };
    const { client, requests } = fakeApi(
      { "POST /api/v1/presets": () => jsonResponse(presetBody(), 201) },
      { csrfToken: "tok" },
    );

    const created = await createPreset(client, draft);

    expect(created.id).toBe(PRESET_ID);
    expect(await requests[0]?.json()).toEqual(draft);
    expect(requests[0]?.headers.get("X-CSRF-Token")).toBe("tok");
  });
});

describe("deletePreset", () => {
  it("manda If-Match com a revisão entre aspas (ETag documentado do preset)", async () => {
    const { client, requests } = fakeApi({
      [`DELETE /api/v1/presets/${PRESET_ID}`]: () => new Response(null, { status: 204 }),
    });

    await deletePreset(client, presetBody({ revision: 3 }));

    expect(requests[0]?.headers.get("If-Match")).toBe('"3"');
  });

  it("revisão vencida chega a quem chamou como 412", async () => {
    const { client } = fakeApi({
      [`DELETE /api/v1/presets/${PRESET_ID}`]: () => problemResponse(412, "PRECONDITION_FAILED"),
    });

    await expect(deletePreset(client, presetBody())).rejects.toMatchObject({
      failure: { kind: "problem", status: 412 },
    });
  });

  it("preset que já não existe (404) conta como excluído", async () => {
    const { client } = fakeApi({
      [`DELETE /api/v1/presets/${PRESET_ID}`]: () => problemResponse(404, "NOT_FOUND"),
    });

    await expect(deletePreset(client, presetBody())).resolves.toBeUndefined();
  });
});

describe("replacePreset", () => {
  const draft: PresetDraft = {
    name: "Clientes de junho",
    recipe_id: "csv-janitor",
    recipe_version: 1,
    parameters: { output_delimiter: ";" },
  };

  it("PUT manda o If-Match da revisão e devolve o preset novo", async () => {
    const { client, requests } = fakeApi(
      {
        [`PUT /api/v1/presets/${PRESET_ID}`]: () =>
          jsonResponse(presetBody({ name: "Clientes de junho", revision: 4 })),
      },
      { csrfToken: "tok" },
    );

    const replaced = await replacePreset(client, presetBody({ revision: 3 }), draft);

    expect(replaced).toMatchObject({ name: "Clientes de junho", revision: 4 });
    expect(requests[0]?.headers.get("If-Match")).toBe('"3"');
    expect(requests[0]?.headers.get("X-CSRF-Token")).toBe("tok");
    expect(await requests[0]?.json()).toEqual(draft);
  });

  it("404 no PUT é falha, não sucesso", async () => {
    const { client } = fakeApi({
      [`PUT /api/v1/presets/${PRESET_ID}`]: () => problemResponse(404, "NOT_FOUND"),
    });

    await expect(replacePreset(client, presetBody(), draft)).rejects.toBeInstanceOf(ApiError);
  });
});
