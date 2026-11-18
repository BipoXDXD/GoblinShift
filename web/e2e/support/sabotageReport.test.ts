import { describe, expect, it } from "vitest";

import { inspectSabotageReport } from "./sabotageReport.ts";

function outcome(status: string, ...results: string[]) {
  return { status, results: results.map((result) => ({ status: result })) };
}

function report(tests: readonly ReturnType<typeof outcome>[], stats = {}, errors: unknown[] = []) {
  return {
    suites: [{ specs: [], suites: [{ specs: [{ tests }] }] }],
    stats: { expected: 0, unexpected: 0, flaky: 0, skipped: 0, ...stats },
    errors,
  };
}

const failed = outcome("unexpected", "failed");

describe("relatório do Chromium com defeito plantado", () => {
  it("confirma o defeito com todos os testes executados e uma falha", () => {
    const raw = report([failed, outcome("expected", "passed")], { unexpected: 1, expected: 1 });

    expect(inspectSabotageReport(raw, 1)).toEqual({
      caught: true,
      selected: 2,
      executed: 2,
      expected: 1,
      unexpected: 1,
      failed: 1,
      flaky: 0,
      skipped: 0,
      errors: 0,
    });
  });

  it.each([
    { name: "pulado", results: ["skipped"] },
    { name: "sem resultado", results: [] },
    { name: "interrompido", results: ["interrupted"] },
  ])("reprova teste $name mesmo com outra falha", ({ results }) => {
    const raw = report([failed, outcome("skipped", ...results)], { unexpected: 1, skipped: 1 });

    expect(inspectSabotageReport(raw, 1)).toMatchObject({
      caught: false,
      selected: 2,
      executed: 1,
      skipped: 1,
    });
  });

  it("reprova resultado sem execução ainda que a contagem o classifique como esperado", () => {
    const raw = report([failed, outcome("expected")], { unexpected: 1, expected: 1 });

    expect(inspectSabotageReport(raw, 1)).toMatchObject({
      caught: false,
      executed: 1,
      selected: 2,
    });
  });

  it("reprova uma tentativa interrompida mesmo depois de uma falha registrada", () => {
    const raw = report([outcome("unexpected", "failed", "interrupted")], { unexpected: 1 });

    expect(inspectSabotageReport(raw, 1)).toMatchObject({
      caught: false,
      selected: 1,
      executed: 0,
    });
  });

  it("reprova seleção vazia e cenário sem falha detectada", () => {
    const clean = report([outcome("expected", "passed")], { expected: 1 });

    expect(inspectSabotageReport(report([]), 1)).toMatchObject({ caught: false, selected: 0 });
    expect(inspectSabotageReport(clean, 0)).toMatchObject({ caught: false, selected: 1 });
    const unexpectedlyPassed = report([outcome("unexpected", "passed")], { unexpected: 1 });
    expect(inspectSabotageReport(unexpectedlyPassed, 1)).toMatchObject({
      caught: false,
      failed: 0,
    });
  });

  it("reprova inconsistência entre os testes e a estatística", () => {
    expect(() => inspectSabotageReport(report([failed]), 1)).toThrow(/contagem inconsistente/);
  });

  it("reprova erro global, processo interrompido ou resultado instável", () => {
    const withFailure = report([failed], { unexpected: 1 });
    const withError = report([failed], { unexpected: 1 }, [{ message: "erro global" }]);
    const flaky = report([failed, outcome("flaky", "failed", "passed")], {
      unexpected: 1,
      flaky: 1,
    });

    expect(inspectSabotageReport(withError, 1)).toMatchObject({ caught: false, errors: 1 });
    expect(inspectSabotageReport(withFailure, null)).toMatchObject({ caught: false });
    expect(inspectSabotageReport(flaky, 1)).toMatchObject({ caught: false, flaky: 1 });
  });
});
