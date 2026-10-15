import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PROBLEM_CODES, type ApiProblem } from "../../api/problem";
import { axeViolations } from "../../test/axe";
import { describeFailure, ProblemAlert } from "./ProblemAlert";

const problem = (code: ApiProblem["code"], status: number): ApiProblem => ({
  type: "https://goblinshift.dev/problems/x",
  title: "Title from server",
  status,
  detail: '{"raw":"detail from server"}',
  instance: "/api/v1/runs",
  code,
  request_id: "req-123",
});

describe("describeFailure", () => {
  it.each(PROBLEM_CODES)("tem texto próprio para %s", (code) => {
    const description = describeFailure({
      kind: "problem",
      status: 400,
      problem: problem(code, 400),
      retryAfterMs: null,
    });

    expect(description.message).not.toBe("");
    expect(description.message).not.toContain("detail from server");
    expect(description.message).not.toContain("Title from server");
  });

  it("acrescenta a espera do Retry-After, arredondada para cima", () => {
    const description = describeFailure({
      kind: "problem",
      status: 429,
      problem: problem("RATE_LIMITED", 429),
      retryAfterMs: 1_500,
    });

    expect(description.message).toMatch(/Tente de novo em 2 s\.$/);
  });

  it.each([
    [59_000, /Tente de novo em 59 s\.$/],
    [60_000, /Tente de novo em 1 minuto\.$/],
    [61_000, /Tente de novo em 2 minutos\.$/],
    [299_000, /Tente de novo em 5 minutos\.$/],
  ])("de um minuto em diante, a espera de %i ms sai em minutos", (retryAfterMs, expected) => {
    const description = describeFailure({
      kind: "problem",
      status: 503,
      problem: problem("SERVICE_UNAVAILABLE", 503),
      retryAfterMs,
    });

    expect(description.message).toMatch(expected);
  });

  it("no teto de 5 min o Retry-After foi cortado, então a espera não é prometida", () => {
    const description = describeFailure({
      kind: "problem",
      status: 429,
      problem: problem("QUOTA_EXCEEDED", 429),
      retryAfterMs: 300_000,
    });

    expect(description.message).toBe(
      "Você atingiu a cota da conta. Libere espaço ou espere a cota renovar.",
    );
  });

  it("usa o texto do contexto no lugar do texto geral do código", () => {
    const description = describeFailure(
      { kind: "problem", status: 409, problem: problem("STATE_CONFLICT", 409), retryAfterMs: null },
      { STATE_CONFLICT: "Texto da tela." },
    );

    expect(description.message).toBe("Texto da tela.");
  });

  it("guarda o request_id para suporte", () => {
    const description = describeFailure({
      kind: "problem",
      status: 500,
      problem: problem("INTERNAL_ERROR", 500),
      retryAfterMs: null,
    });

    expect(description.requestId).toBe("req-123");
  });

  it.each([
    [{ kind: "network" } as const, /conexão/],
    [{ kind: "timeout" } as const, /demorou/],
    [{ kind: "unexpected-response", status: 502, retryAfterMs: null } as const, /inesperado/],
    [{ kind: "internal" } as const, /Recarregue/],
  ])("descreve a falha sem problem+json (%o)", (failure, expected) => {
    const description = describeFailure(failure);

    expect(description.message).toMatch(expected);
    expect(description.requestId).toBeNull();
  });
});

describe("ProblemAlert", () => {
  it("mostra o texto próprio e o código do pedido, nunca o JSON do servidor", () => {
    render(
      <ProblemAlert
        failure={{
          kind: "problem",
          status: 409,
          problem: problem("ACTIVE_RUN_LIMIT", 409),
          retryAfterMs: null,
        }}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Você já tem uma execução em andamento.");
    expect(alert).toHaveTextContent("req-123");
    expect(alert).not.toHaveTextContent("{");
    expect(alert).not.toHaveTextContent("detail from server");
  });

  it("não tem violações do axe", async () => {
    const { container } = render(<ProblemAlert failure={{ kind: "network" }} />);

    expect(await axeViolations(container)).toEqual([]);
  });
});
