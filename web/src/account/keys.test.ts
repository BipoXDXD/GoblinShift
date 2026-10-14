import { describe, expect, it } from "vitest";

import { apiKeyBody } from "../test/account";
import { SCOPES } from "./api";
import { keyStatus, parseNewKey } from "./keys";

const NOW = Date.parse("2026-10-14T12:00:00Z");

describe("keyStatus", () => {
  it("ativa antes de vencer", () => {
    expect(keyStatus(apiKeyBody({ expires_at: "2026-10-14T12:00:01Z" }), NOW)).toBe("active");
  });

  it("expirada no instante do vencimento e depois", () => {
    expect(keyStatus(apiKeyBody({ expires_at: "2026-10-14T12:00:00Z" }), NOW)).toBe("expired");
    expect(keyStatus(apiKeyBody({ expires_at: "2026-10-01T00:00:00Z" }), NOW)).toBe("expired");
  });

  it("revogada mesmo se já venceu", () => {
    const key = apiKeyBody({
      expires_at: "2026-10-01T00:00:00Z",
      revoked_at: "2026-09-30T00:00:00Z",
    });
    expect(keyStatus(key, NOW)).toBe("revoked");
  });
});

describe("parseNewKey", () => {
  const all = new Set(SCOPES);

  it("monta o comando com os escopos na ordem da lista", () => {
    const parsed = parseNewKey(
      { scopes: new Set(["runs:write", "files:read"]), days: "30" },
      SCOPES,
    );

    expect(parsed).toEqual({
      ok: true,
      command: { scopes: ["files:read", "runs:write"], days: 30 },
    });
  });

  it.each([
    ["1", 1],
    ["90", 90],
    [" 45 ", 45],
  ])("aceita %j dias", (days, expected) => {
    expect(parseNewKey({ scopes: all, days }, SCOPES)).toMatchObject({
      ok: true,
      command: { days: expected },
    });
  });

  it.each(["0", "91", "", "-1", "1.5", "trinta", "1e1", "0090", "\u0000"])(
    "recusa %j dias",
    (days) => {
      expect(parseNewKey({ scopes: all, days }, SCOPES)).toEqual({
        ok: false,
        errors: { scopes: null, days: "Informe um número inteiro de 1 a 90." },
      });
    },
  );

  it("sem escopo, aponta o grupo", () => {
    expect(parseNewKey({ scopes: new Set(), days: "30" }, SCOPES)).toEqual({
      ok: false,
      errors: { scopes: "Escolha pelo menos um escopo.", days: null },
    });
  });

  it("os dois erros juntos", () => {
    const parsed = parseNewKey({ scopes: new Set(), days: "x" }, SCOPES);

    expect(parsed).toEqual({
      ok: false,
      errors: {
        scopes: "Escolha pelo menos um escopo.",
        days: "Informe um número inteiro de 1 a 90.",
      },
    });
  });
});
