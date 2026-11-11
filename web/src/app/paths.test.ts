import { describe, expect, it } from "vitest";

import { acceptTermsPath, runPath, signInPath, withoutTrailingSlash, workbenchPath } from "./paths";

describe("paths", () => {
  it("monta a rota da Bancada da receita", () => {
    expect(workbenchPath("csv-janitor")).toBe("/bancada/csv-janitor");
  });

  it("escapa o id da execução", () => {
    expect(runPath("a/b")).toBe("/execucoes/a%2Fb");
  });

  it("leva o caminho de volta ao aceite dos termos e ao Entrar", () => {
    expect(acceptTermsPath("/historico?estado=failed")).toBe(
      "/termos/aceitar?volta=%2Fhistorico%3Festado%3Dfailed",
    );
    expect(signInPath("/acesso")).toBe("/entrar?volta=%2Facesso");
  });

  it("sem parâmetro quando a volta é a Oficina", () => {
    expect(acceptTermsPath("/")).toBe("/termos/aceitar");
    expect(signInPath("/")).toBe("/entrar");
  });

  it.each([
    ["/termos/", "/termos"],
    ["/termos//", "/termos"],
    ["/termos/aceitar/", "/termos/aceitar"],
    // Codificadas, as barras e a contrabarra são texto do caminho local, não outro host.
    ["/%2F%2Fevil.com/", "/%2F%2Fevil.com"],
    ["/%5Cevil.com/", "/%5Cevil.com"],
  ])("tira a barra final de %s", (pathname, expected) => {
    expect(withoutTrailingSlash(pathname)).toBe(expected);
  });

  it.each(["/", "/termos", "/termos/aceitar"])("%s já é canônico", (pathname) => {
    expect(withoutTrailingSlash(pathname)).toBeNull();
  });

  // Sem a barra final, `//evil.com/` viraria `//evil.com`, que o navegador lê como outro site.
  it.each(["//evil.com/", "/\\evil.com/", "///", "//"])(
    "%s não vira destino fora do site",
    (pathname) => {
      expect(withoutTrailingSlash(pathname)).toBeNull();
    },
  );
});
