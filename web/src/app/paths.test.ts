import { describe, expect, it } from "vitest";

import { acceptTermsPath, runPath, signInPath, workbenchPath } from "./paths";

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
});
