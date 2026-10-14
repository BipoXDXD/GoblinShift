import { describe, expect, it } from "vitest";

import { formatBytes, formatDateTime } from "./format";

describe("formatBytes", () => {
  it.each([
    [0, "0 bytes"],
    [999, "999 bytes"],
    [1_000, "1 kB"],
    [1_500, "1,5 kB"],
    [12_345_678, "12,3 MB"],
    [2_000_000_000, "2 GB"],
  ])("%i → %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});

describe("formatDateTime", () => {
  it("formata data e hora curtas no idioma da interface", () => {
    expect(formatDateTime("2026-10-21T12:00:00Z")).toMatch(/^21\/10\/2026,? \d{2}:\d{2}$/);
  });
});
