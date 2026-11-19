import { describe, expect, it } from "vitest";

import { inspectSabotageReport } from "./sabotageReport.ts";

const scenario = {
  spec: "contrast.spec.ts",
  project: "desktop",
  assertions: ["contrast-light"],
};

function outcome(status: string, ...results: string[]) {
  return {
    status,
    expectedStatus: "passed",
    projectName: "desktop",
    results: results.map((result) => ({ status: result, errors: [] })),
  };
}

function failureWithMessage(message: string, extraErrors = 0) {
  const error = { message };
  return {
    ...outcome("unexpected"),
    results: [
      {
        status: "failed",
        error,
        errors: [error, ...Array.from({ length: extraErrors }, () => ({ message: "teardown" }))],
      },
    ],
  };
}

function report(tests: readonly unknown[], stats = {}, errors: unknown[] = []) {
  return {
    suites: [{ specs: [], suites: [{ specs: [{ file: "contrast.spec.ts", tests }] }] }],
    stats: { expected: 0, unexpected: 0, flaky: 0, skipped: 0, ...stats },
    errors,
  };
}

const failed = failureWithMessage("Error: [sabotage:contrast-light]\n\nexpect(received).toEqual(expected)");

describe("relatório do Chromium com defeito plantado", () => {
  it("confirma o defeito com todos os testes executados e uma falha", () => {
    const raw = report([failed, outcome("expected", "passed")], { unexpected: 1, expected: 1 });

    expect(inspectSabotageReport(raw, 1, scenario)).toEqual({
      caught: true,
      selected: 2,
      executed: 2,
      expected: 1,
      unexpected: 1,
      failed: 1,
      attributed: 1,
      unattributed: [],
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

    expect(inspectSabotageReport(raw, 1, scenario)).toMatchObject({
      caught: false,
      selected: 2,
      executed: 1,
      skipped: 1,
    });
  });

  it("reprova resultado sem execução ainda que a contagem o classifique como esperado", () => {
    const raw = report([failed, outcome("expected")], { unexpected: 1, expected: 1 });

    expect(inspectSabotageReport(raw, 1, scenario)).toMatchObject({
      caught: false,
      executed: 1,
      selected: 2,
    });
  });

  it("reprova uma tentativa interrompida mesmo depois de uma falha registrada", () => {
    const raw = report([outcome("unexpected", "failed", "interrupted")], { unexpected: 1 });

    expect(inspectSabotageReport(raw, 1, scenario)).toMatchObject({
      caught: false,
      selected: 1,
      executed: 0,
    });
  });

  it("reprova seleção vazia e cenário sem falha detectada", () => {
    const clean = report([outcome("expected", "passed")], { expected: 1 });

    expect(inspectSabotageReport(report([]), 1, scenario)).toMatchObject({ caught: false, selected: 0 });
    expect(inspectSabotageReport(clean, 0, scenario)).toMatchObject({ caught: false, selected: 1 });
    const unexpectedlyPassed = report([outcome("unexpected", "passed")], { unexpected: 1 });
    expect(inspectSabotageReport(unexpectedlyPassed, 1, scenario)).toMatchObject({
      caught: false,
      failed: 0,
    });
  });

  it.each([
    { name: "falha de setup", result: failureWithMessage("Error: browserType.launch: navegador indisponível") },
    {
      name: "marcador de outro defeito",
      result: failureWithMessage("Error: [sabotage:contrast-dark]\n\nexpect(received).toEqual(expected)"),
    },
    {
      name: "marcador dentro da mensagem de um erro genérico",
      result: failureWithMessage("Error: erro de fixture\nError: [sabotage:contrast-light]\n\nquadro de código"),
    },
    {
      name: "erro extra no teardown",
      result: failureWithMessage("Error: [sabotage:contrast-light]\n\nexpect(received).toEqual(expected)", 1),
    },
    { name: "timeout do teste", result: outcome("unexpected", "timedOut") },
    { name: "projeto fora do cenário", result: { ...failed, projectName: "mobile" } },
    { name: "falha em teste com falha esperada", result: { ...failed, expectedStatus: "failed" } },
  ])("reprova $name mesmo com outra falha atribuída", ({ result }) => {
    const raw = report([failed, result], { unexpected: 2 });

    expect(inspectSabotageReport(raw, 1, scenario)).toMatchObject({
      caught: false,
      attributed: 1,
    });
  });

  it("reprova relatório de outra spec", () => {
    const raw = report([failed], { unexpected: 1 });

    expect(inspectSabotageReport(raw, 1, { ...scenario, spec: "focus.spec.ts" })).toMatchObject({
      caught: false,
      attributed: 0,
    });
  });

  it("reprova inconsistência entre os testes e a estatística", () => {
    expect(() => inspectSabotageReport(report([failed]), 1, scenario)).toThrow(/contagem inconsistente/);
  });

  it("reprova erro global, processo interrompido ou resultado instável", () => {
    const withFailure = report([failed], { unexpected: 1 });
    const withError = report([failed], { unexpected: 1 }, [{ message: "erro global" }]);
    const flaky = report([failed, outcome("flaky", "failed", "passed")], {
      unexpected: 1,
      flaky: 1,
    });

    expect(inspectSabotageReport(withError, 1, scenario)).toMatchObject({ caught: false, errors: 1 });
    expect(inspectSabotageReport(withFailure, null, scenario)).toMatchObject({ caught: false });
    expect(inspectSabotageReport(flaky, 1, scenario)).toMatchObject({ caught: false, flaky: 1 });
  });
});
