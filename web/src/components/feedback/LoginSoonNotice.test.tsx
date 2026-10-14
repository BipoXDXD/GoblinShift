import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ApiFailure } from "../../api/response";
import { axeViolations } from "../../test/axe";
import { isLoginMissing, LoginSoonNotice } from "./LoginSoonNotice";

function problem(code: "AUTHENTICATION_REQUIRED" | "REAUTH_REQUIRED" | "FORBIDDEN"): ApiFailure {
  return {
    kind: "problem",
    status: code === "FORBIDDEN" ? 403 : 401,
    retryAfterMs: null,
    problem: {
      type: "x",
      title: "x",
      status: 401,
      detail: "x",
      instance: "/x",
      code,
      request_id: "r",
    },
  };
}

describe("LoginSoonNotice", () => {
  it("é um aviso com título, não um alerta de erro", () => {
    render(<LoginSoonNotice />);

    expect(screen.getByRole("region", { name: "O login chega em breve" })).toHaveTextContent(
      "Por enquanto, as receitas rodam pela API, com uma chave de API.",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("não tem violações do axe", async () => {
    const { container } = render(<LoginSoonNotice />);

    expect(await axeViolations(container)).toEqual([]);
  });
});

describe("isLoginMissing", () => {
  it("só AUTHENTICATION_REQUIRED é falta de login", () => {
    expect(isLoginMissing(problem("AUTHENTICATION_REQUIRED"))).toBe(true);
    expect(isLoginMissing(problem("REAUTH_REQUIRED"))).toBe(false);
    expect(isLoginMissing(problem("FORBIDDEN"))).toBe(false);
    expect(isLoginMissing({ kind: "unexpected-response", status: 401, retryAfterMs: null })).toBe(
      false,
    );
    expect(isLoginMissing({ kind: "network" })).toBe(false);
  });
});
