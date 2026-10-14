import { describe, expect, expectTypeOf, it } from "vitest";

import type { components } from "./schema";
import { parseProblem, type ApiProblem, type PROBLEM_CODES } from "./problem";

const problem = {
  type: "https://goblinshift.dev/problems/rate-limited",
  title: "Muitas requisições",
  status: 429,
  detail: "Limite de transferências simultâneas atingido.",
  instance: "/api/v1/uploads/1/content",
  code: "RATE_LIMITED",
  request_id: "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a",
};

describe("parseProblem", () => {
  it("aceita o problem+json do catálogo", () => {
    expect(parseProblem(problem)).toEqual(problem);
  });

  it("mantém os erros por campo", () => {
    const errors = [{ field: "body.items.0.size", code: "invalid", message: "negativo" }];

    expect(parseProblem({ ...problem, errors })?.errors).toEqual(errors);
  });

  it("descarta campos desconhecidos (leitor tolerante)", () => {
    expect(parseProblem({ ...problem, extra: "x" })).not.toHaveProperty("extra");
  });

  it.each([
    ["null", null],
    ["texto", "erro"],
    ["code fora do catálogo", { ...problem, code: "NOVO_CODIGO" }],
    ["sem request_id", { ...problem, request_id: undefined }],
    ["status como texto", { ...problem, status: "429" }],
    ["errors malformado", { ...problem, errors: [{ field: 1 }] }],
  ])("recusa %s", (_label, raw) => {
    expect(parseProblem(raw)).toBeNull();
  });
});

describe("tipos do problem+json", () => {
  it("lista exatamente os códigos da spec", () => {
    expectTypeOf<(typeof PROBLEM_CODES)[number]>().toEqualTypeOf<
      components["schemas"]["ProblemCode"]
    >();
  });

  it("produz um tipo atribuível ao ProblemDetails gerado", () => {
    expectTypeOf<ApiProblem>().toExtend<components["schemas"]["ProblemDetails"]>();
  });
});
