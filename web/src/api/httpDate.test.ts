import { describe, expect, it } from "vitest";

import { parseHttpDateMs } from "./httpDate";

describe("parseHttpDateMs", () => {
  it.each([
    ["Wed, 14 Oct 2026 15:00:00 GMT", Date.parse("2026-10-14T15:00:00Z")],
    ["  Wed, 14 Oct 2026 15:00:00 GMT  ", Date.parse("2026-10-14T15:00:00Z")],
  ])("converte a HTTP-date %j em ms", (value, expected) => {
    expect(parseHttpDateMs(value)).toBe(expected);
  });

  it.each([
    ["sem GMT", "Wed, 14 Oct 2026 15:00:00"],
    ["ISO 8601", "2026-10-14T15:00:00Z"],
    ["lixo", "amanhã GMT"],
    ["vazio", ""],
    ["só espaços", "   "],
  ])("recusa %s", (_case, value) => {
    expect(parseHttpDateMs(value)).toBeNull();
  });
});
