import { describe, expect, it } from "vitest";

import { ApiError } from "../api/response";
import { loggableError } from "./errorLog";

const CANARY = "CANARY-dado-do-usuario";

describe("loggableError", () => {
  it("ApiError com problem registra só kind, status, code e request_id", () => {
    const error = new ApiError({
      kind: "problem",
      status: 422,
      retryAfterMs: null,
      problem: {
        type: "https://goblinshift.dev/problems/validation_failed",
        title: "Validation failed",
        status: 422,
        detail: `arquivo ${CANARY}.csv inválido`,
        instance: `/api/v1/uploads/${CANARY}`,
        code: "VALIDATION_FAILED",
        request_id: "req-1",
        errors: [{ field: "file_name", code: "invalid", message: CANARY }],
      },
    });

    const logged = loggableError(error);

    expect(logged).toEqual({
      name: "ApiError",
      kind: "problem",
      status: 422,
      code: "VALIDATION_FAILED",
      request_id: "req-1",
    });
    expect(JSON.stringify(logged)).not.toContain(CANARY);
  });

  it("ApiError sem problem registra só o nome, o kind e o status", () => {
    const error = new ApiError({ kind: "unexpected-response", status: 502, retryAfterMs: null });

    expect(loggableError(error)).toEqual({
      name: "ApiError",
      kind: "unexpected-response",
      status: 502,
    });
  });

  it("ApiError de rede registra só o nome e o kind", () => {
    expect(loggableError(new ApiError({ kind: "network" }))).toEqual({
      name: "ApiError",
      kind: "network",
    });
  });

  it("Error registra só o nome e a mensagem, sem propriedades extras nem stack", () => {
    const error = Object.assign(new TypeError("falhou"), { segredo: CANARY });

    const logged = loggableError(error);

    expect(logged).toEqual({ name: "TypeError", message: "falhou" });
    expect(JSON.stringify(logged)).not.toContain(CANARY);
  });

  it("resposta de erro do router registra só o status", () => {
    // A forma que o isRouteErrorResponse reconhece (o 404 de rota inexistente, por exemplo).
    const error = { status: 404, statusText: "Not Found", internal: true, data: CANARY };

    const logged = loggableError(error);

    expect(logged).toEqual({ type: "route-error-response", status: 404 });
    expect(JSON.stringify(logged)).not.toContain(CANARY);
  });

  it.each([
    ["string", CANARY, "string"],
    ["objeto", { segredo: CANARY }, "object"],
    ["undefined", undefined, "undefined"],
  ])("valor que não é Error (%s) registra só o tipo", (_label, value, type) => {
    const logged = loggableError(value);

    expect(logged).toEqual({ type });
    expect(JSON.stringify(logged)).not.toContain(CANARY);
  });
});
