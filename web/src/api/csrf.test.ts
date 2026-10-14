import { describe, expect, it } from "vitest";

import { csrfTokenFromCookies } from "./csrf";

describe("csrfTokenFromCookies", () => {
  it("lê o token do cookie __Host-gs_csrf", () => {
    expect(csrfTokenFromCookies("a=1; __Host-gs_csrf=tok-123; b=2")).toBe("tok-123");
  });

  it("decodifica o valor percent-encoded", () => {
    expect(csrfTokenFromCookies("__Host-gs_csrf=a%2Bb")).toBe("a+b");
  });

  it("devolve null sem o cookie", () => {
    expect(csrfTokenFromCookies("gs_csrf=falso; outro=1")).toBeNull();
  });

  it("devolve null para cookies vazios", () => {
    expect(csrfTokenFromCookies("")).toBeNull();
  });

  it("devolve null para valor vazio", () => {
    expect(csrfTokenFromCookies("__Host-gs_csrf=")).toBeNull();
  });

  it("devolve null para valor com percent-encoding inválido", () => {
    expect(csrfTokenFromCookies("__Host-gs_csrf=%E0%A4%A")).toBeNull();
  });
});
