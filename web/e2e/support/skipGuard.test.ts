import { describe, expect, it } from "vitest";

import { skippedTests } from "./skipGuard";

function report(statuses: readonly string[], skipped: number): unknown {
  return {
    suites: [
      {
        title: "goblins.spec.ts",
        specs: [],
        suites: [
          {
            title: "Oficina",
            specs: statuses.map((status, index) => ({
              title: `caso ${String(index)}`,
              tests: [{ projectName: "desktop", status }],
            })),
          },
        ],
      },
    ],
    stats: { expected: 0, skipped, unexpected: 0, flaky: 0 },
  };
}

describe("guard de testes pulados do Playwright", () => {
  it("lista o pulado com projeto, arquivo, suíte e título", () => {
    expect(skippedTests(report(["expected", "skipped"], 1))).toEqual([
      "desktop › goblins.spec.ts › Oficina › caso 1",
    ]);
  });

  it("não acusa execução sem pulados, nem falha ou instável", () => {
    expect(skippedTests(report(["expected", "unexpected", "flaky"], 0))).toEqual([]);
  });

  it("recusa relatório cuja contagem de pulados não bate com os testes", () => {
    expect(() => skippedTests(report(["expected"], 1))).toThrow(/inconsistente/);
  });

  it("recusa o que não é relatório do reporter json", () => {
    expect(() => skippedTests({ suites: [] })).toThrow();
  });
});
