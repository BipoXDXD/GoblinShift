import { describe, expect, it } from "vitest";

import { parseRetryAfterMs } from "./retryAfter";

const NOW_MS = Date.parse("2026-10-14T12:00:00Z");

describe("parseRetryAfterMs", () => {
  it.each([
    ["0", 0],
    ["1", 1_000],
    ["60", 60_000],
    [" 2 ", 2_000],
  ])("converte delta-seconds %j em milissegundos", (header, expected) => {
    expect(parseRetryAfterMs(header, NOW_MS)).toBe(expected);
  });

  it("converte HTTP-date futura na distância até ela", () => {
    expect(parseRetryAfterMs("Wed, 14 Oct 2026 12:00:30 GMT", NOW_MS)).toBe(30_000);
  });

  it("trata HTTP-date passada como pode tentar já", () => {
    expect(parseRetryAfterMs("Wed, 14 Oct 2026 11:59:00 GMT", NOW_MS)).toBe(0);
  });

  it("devolve null sem header", () => {
    expect(parseRetryAfterMs(null, NOW_MS)).toBeNull();
  });

  it.each(["", "-1", "1.5", "2s", "abc", "1e3", "Wed, 99 Foo 2026"])(
    "devolve null para valor inválido %j",
    (header) => {
      expect(parseRetryAfterMs(header, NOW_MS)).toBeNull();
    },
  );
});
