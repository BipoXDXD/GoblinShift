import { describe, expect, it } from "vitest";

import { remainingLabel, remainingMinutes } from "./retention";

const NOW = Date.parse("2026-10-14T12:00:00Z");
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

function iso(offsetMs: number): string {
  return new Date(NOW + offsetMs).toISOString();
}

describe("remainingLabel", () => {
  it.each([
    ["47 h 10 min", 47 * HOUR + 10 * MINUTE, { hours: 48, urgent: false }],
    ["6 h", 6 * HOUR, { hours: 6, urgent: true }],
    ["6 h 1 min", 6 * HOUR + MINUTE, { hours: 7, urgent: false }],
    ["30 min", 30 * MINUTE, { hours: 1, urgent: true }],
  ])("expira em %s", (_label, offset, expected) => {
    expect(remainingLabel(iso(offset), NOW)).toEqual(expected);
  });

  it.each([
    ["vencido", -MINUTE],
    ["vencendo agora", 0],
  ])("%s não tem prazo", (_label, offset) => {
    expect(remainingLabel(iso(offset), NOW)).toBeNull();
  });

  it("data ilegível não tem prazo", () => {
    expect(remainingLabel("não é data", NOW)).toBeNull();
  });
});

describe("remainingMinutes", () => {
  it("arredonda para cima", () => {
    expect(remainingMinutes(iso(29 * MINUTE + 1), NOW)).toBe(30);
  });
});
