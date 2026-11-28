import { describe, expect, it } from "vitest";

import { formatBytes, formatCount, formatDateTime } from "./format";

describe("formatBytes", () => {
  it.each([
    [0, "0 bytes"],
    [1_023, "1.023 bytes"],
    [1_024, "1 KB"],
    [1_536, "1,5 KB"],
    [50 * 1_024 ** 2, "50 MB"],
    [2 * 1_024 ** 3, "2 GB"],
    [2_000_000_000, "1,9 GB"],
  ])("%i → %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});

describe("formatDateTime", () => {
  it("formata data e hora curtas no idioma da interface", () => {
    expect(formatDateTime("2026-10-21T12:00:00Z")).toMatch(/^21\/10\/2026,? \d{2}:\d{2}$/);
  });
});

describe("formatCount", () => {
  it("separa os milhares como em pt-BR", () => {
    expect(formatCount(1200)).toBe("1.200");
    expect(formatCount(0)).toBe("0");
  });
});
