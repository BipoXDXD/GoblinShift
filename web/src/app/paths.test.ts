import { describe, expect, it } from "vitest";

import { runPath, workbenchPath } from "./paths";

describe("paths", () => {
  it("monta a rota da Bancada da receita", () => {
    expect(workbenchPath("csv-janitor")).toBe("/bancada/csv-janitor");
  });

  it("escapa o id da execução", () => {
    expect(runPath("a/b")).toBe("/execucoes/a%2Fb");
  });
});
