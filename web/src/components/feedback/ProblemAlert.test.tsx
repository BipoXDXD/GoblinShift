import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PROBLEM_CODES, type ApiProblem } from "../../api/problem";
import { axeViolations } from "../../test/axe";
import { TEXT } from "../../text";
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

  // Frases literais (M3 da auditoria 5e): cada uma diz o que aconteceu e o que a pessoa pode fazer.
  // Escritas à mão, nunca lidas do text.ts, para o teste falhar se uma frase perder a ação.
  it.each([
    [
      "BODY_TOO_LARGE",
      413,
      "O arquivo ou o pedido passa do tamanho permitido. Confira os limites da receita e envie arquivos menores.",
    ],
    [
      "UNSUPPORTED_MEDIA_TYPE",
      415,
      "Este tipo de arquivo não é aceito aqui. Escolha um dos formatos da receita.",
    ],
    [
      "INSUFFICIENT_SCOPE",
      403,
      "A chave usada não tem permissão para esta ação. Crie uma chave com o escopo necessário no Acesso.",
    ],
    [
      "FORBIDDEN",
      403,
      "Você não tem permissão para esta ação. Confira se entrou com a conta certa.",
    ],
    [
      "NOT_FOUND",
      404,
      "Não encontramos este item. Ele pode ter sido removido ou ter expirado. Atualize a página e confira a lista.",
    ],
    ["GONE", 410, "Isto expirou e não está mais disponível. Comece de novo pela Oficina."],
    [
      "METHOD_NOT_ALLOWED",
      405,
      "Esta ação não é aceita aqui. Recarregue a página e tente de novo.",
    ],
  ] as const)("%s diz o que houve e o que fazer", (code, status, expected) => {
    const description = describeFailure({
      kind: "problem",
      status,
      problem: problem(code, status),
      retryAfterMs: null,
    });

    expect(description.message).toBe(expected);
  });

  // O envio de arquivos e a lista de exclusões mostram só a frase, sem o código nem o link: uma frase
  // que mandasse escrever ao suporte apontaria para um canal que essas telas não oferecem.
  it.each(PROBLEM_CODES)("a frase de %s não depende do link do suporte", (code) => {
    const description = describeFailure({
      kind: "problem",
      status: 400,
      problem: problem(code, 400),
      retryAfterMs: null,
    });

    expect(description.message).not.toMatch(/suporte/i);
  });

  it("erro de campo sem texto próprio diz onde conferir o formato", () => {
    expect(TEXT.workbench.serverFieldErrors.fallback).toBe(
      "O servidor recusou este valor. Confira o formato pedido na descrição do campo.",
    );
  });

  it("erro da própria interface diz de onde veio e pede para recarregar", () => {
    expect(describeFailure({ kind: "internal" }).message).toBe(
      "Houve um erro na interface. Recarregue a página e tente de novo.",
    );
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

  it("com código do pedido, oferece escrever para o suporte com o código no assunto e no corpo", () => {
    const requestId = "req 1&x=2?y#z";
    render(
      <ProblemAlert
        failure={{
          kind: "problem",
          status: 500,
          problem: { ...problem("INTERNAL_ERROR", 500), request_id: requestId },
          retryAfterMs: null,
        }}
      />,
    );

    const link = screen.getByRole("link", { name: "Escrever para o suporte por e-mail" });
    expect(link).toHaveAttribute(
      "href",
      "mailto:contato@goblinshift.dev" +
        "?subject=Suporte%20GoblinShift%3A%20pedido%20req%201%26x%3D2%3Fy%23z" +
        "&body=C%C3%B3digo%20do%20pedido%3A%20req%201%26x%3D2%3Fy%23z",
    );
  });

  it("sem código do pedido, não oferece o link do suporte", () => {
    render(<ProblemAlert failure={{ kind: "network" }} />);

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("com o link do suporte, não tem violações do axe", async () => {
    const { container } = render(
      <ProblemAlert
        failure={{
          kind: "problem",
          status: 403,
          problem: problem("FORBIDDEN", 403),
          retryAfterMs: null,
        }}
      />,
    );

    expect(await axeViolations(container)).toEqual([]);
  });

  it("não tem violações do axe", async () => {
    const { container } = render(<ProblemAlert failure={{ kind: "network" }} />);

    expect(await axeViolations(container)).toEqual([]);
  });
});
