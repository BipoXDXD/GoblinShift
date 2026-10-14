import { describe, expect, it } from "vitest";

import { safeReturnTo, signInHref } from "./signIn";

describe("safeReturnTo", () => {
  it.each([
    ["/", "", "/"],
    ["/bancada/csv-janitor", "", "/bancada/csv-janitor"],
    ["/historico", "?estado=failed&periodo=7d", "/historico?estado=failed&periodo=7d"],
    ["/historico", "?estado=failed&login_error=failed", "/historico?estado=failed"],
    ["/", "?login_error=failed", "/"],
  ])("%s%s volta para %s", (pathname, search, expected) => {
    expect(safeReturnTo(pathname, search)).toBe(expected);
  });

  it.each([
    ["caminho relativo ao esquema", "//evil.example/x"],
    ["barra invertida", "/\\evil.example"],
    ["sem barra inicial", "evil.example"],
    ["esquema", "javascript:alert(1)"],
    ["espaço", "/a b"],
    ["caractere de controle", "/a\u0000b"],
    ["fragmento", "/a#b"],
  ])("%s vira /", (_name, pathname) => {
    expect(safeReturnTo(pathname)).toBe("/");
  });

  it("aceita 512 caracteres e recusa 513", () => {
    const at = `/${"a".repeat(511)}`;
    expect(safeReturnTo(at)).toBe(at);
    expect(safeReturnTo(`${at}a`)).toBe("/");
  });

  it("parâmetro com caractere fora do padrão sai codificado e continua aceito", () => {
    expect(safeReturnTo("/historico", "?receita=café")).toBe("/historico?receita=caf%C3%A9");
  });
});

describe("signInHref", () => {
  it("leva ao login do provedor com o caminho de volta", () => {
    expect(signInHref("google", { returnTo: "/historico?estado=failed" })).toBe(
      "/auth/login/google?return_to=%2Fhistorico%3Festado%3Dfailed",
    );
  });

  it("pede o login de novo com reauth=1", () => {
    expect(signInHref("github", { returnTo: "/acesso", reauth: true })).toBe(
      "/auth/login/github?return_to=%2Facesso&reauth=1",
    );
  });
});
