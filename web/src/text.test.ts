import { describe, expect, it } from "vitest";

import { TEXT } from "./text";

// Só os textos fixos: as funções (textos com variável) ficam de fora do JSON.
const ALL_TEXT = JSON.stringify(TEXT);

describe("vocabulário da interface", () => {
  it("Termos de Uso é nome de documento, sempre com maiúsculas", () => {
    expect(ALL_TEXT).not.toMatch(/termos de uso/);
    expect(ALL_TEXT).not.toMatch(/Termos de uso/);
  });

  it("o elenco é todo feminino: as goblins, nunca os goblins", () => {
    expect(ALL_TEXT).not.toMatch(/\b(os|Os|pelos|dos|aos) goblins\b/);
  });
});
