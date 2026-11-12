import { describe, expect, it } from "vitest";

import { parseSpecKeys, pickApiKey, specKeyId } from "./specKeys.ts";

describe("specKeyId", () => {
  it("usa só o nome do arquivo", () => {
    expect(specKeyId("/runner/web/e2e/specs/journeys.spec.ts")).toBe("journeys.spec.ts");
  });
});

describe("parseSpecKeys", () => {
  it.each([undefined, ""])("sem valor (%j) não há chave por spec", (raw) => {
    expect(parseSpecKeys(raw).size).toBe(0);
  });

  it("lê o objeto de chaves", () => {
    const keys = parseSpecKeys('{"a.spec.ts":"k1","b.spec.ts":"k2"}');

    expect([...keys]).toEqual([
      ["a.spec.ts", "k1"],
      ["b.spec.ts", "k2"],
    ]);
  });

  it.each(["não é json", "[]", '{"a":1}', '{"a":""}', "null", '"k"'])("recusa %j", (raw) => {
    expect(() => parseSpecKeys(raw)).toThrow(/GS_E2E_API_KEYS/);
  });

  it("não repete a chave na mensagem de JSON inválido", () => {
    expect(() => parseSpecKeys('{"a":"segredo-canario"')).not.toThrow(/segredo-canario/);
  });
});

describe("pickApiKey", () => {
  const keys = parseSpecKeys('{"a.spec.ts":"k1","b.spec.ts":"k2"}');

  it("devolve a chave do arquivo", () => {
    expect(pickApiKey(keys, "unica", "b.spec.ts")).toBe("k2");
  });

  it("sem chaves por spec, cai na chave única", () => {
    expect(pickApiKey(new Map(), "unica", "a.spec.ts")).toBe("unica");
  });

  it("com chaves por spec, spec sem conta é erro e não usa a chave única", () => {
    expect(() => pickApiKey(keys, "unica", "nova.spec.ts")).toThrow("nova.spec.ts");
  });
});
