import { describe, expect, it } from "vitest";

import { remainingLabel } from "../../src/runs/retention.ts";
import { RESULTS_LEFT_MS, RESULTS_URGENT_LEFT_MS, resultsExpiresAt } from "./resultsExpiry.ts";

const NOW = Date.parse("2026-11-28T12:00:00Z");

describe("resultsExpiresAt", () => {
  it("conta a partir do relógio dado", () => {
    expect(resultsExpiresAt(NOW, 3_600_000)).toBe("2026-11-28T13:00:00.000Z");
  });

  // A matriz de telas mede o selo do prazo nos dois tons: sem isso o contraste do âmbar some.
  it("o prazo padrão das telas dá o selo neutro", () => {
    expect(remainingLabel(resultsExpiresAt(NOW, RESULTS_LEFT_MS), NOW)?.urgent).toBe(false);
  });

  it("o prazo curto das telas dá o selo de aviso", () => {
    expect(remainingLabel(resultsExpiresAt(NOW, RESULTS_URGENT_LEFT_MS), NOW)?.urgent).toBe(true);
  });
});
