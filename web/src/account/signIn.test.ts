import { describe, expect, it } from "vitest";

import { loginRefFrom, safeReturnTo, signInHref } from "./signIn";

const REF = "0b8f3c2e-5d4a-4f1b-9c7e-2a6d8e0f1b3c";

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

describe("loginRefFrom", () => {
  it("lê o código de referência do fragmento", () => {
    expect(loginRefFrom(`#ref=${REF}`)).toBe(REF);
  });

  it.each([
    ["vazio", ""],
    ["só o #", "#"],
    ["sem valor", "#ref="],
    ["outro nome", `#codigo=${REF}`],
    ["maiúsculas", `#ref=${REF.toUpperCase()}`],
    ["sobra no fim", `#ref=${REF}x`],
    ["sobra no começo", `#ref=x${REF}`],
    ["texto injetado", "#ref=Sua%20conta%20foi%20bloqueada"],
    ["HTML", "#ref=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E"],
    ["sem hifens", `#ref=${REF.replaceAll("-", "")}`],
    ["controle", `#ref=${REF}%00`],
    ["repetido", `#ref=${REF}&ref=${REF}`],
  ])("%s não vale como código", (_name, hash) => {
    expect(loginRefFrom(hash)).toBeNull();
  });
});
