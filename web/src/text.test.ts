import { describe, expect, it } from "vitest";

import { TEXT } from "./text";

// Percorre o módulo inteiro. Dos textos com variável entra o código-fonte da função, que traz os
// trechos fixos do template; os argumentos não importam para o vocabulário.
type TextTree =
  | string
  | ((...args: never[]) => string)
  | readonly TextTree[]
  | { readonly [key: string]: TextTree };

function collectTexts(value: TextTree): string[] {
  if (typeof value === "string") {
    return [value];
  }
  if (typeof value === "function") {
    return [value.toString()];
  }
  return Object.values(value).flatMap(collectTexts);
}

const ALL_TEXTS = collectTexts(TEXT);

describe("vocabulário da interface", () => {
  it("percorre também os textos que são funções", () => {
    expect(ALL_TEXTS).toContainEqual(expect.stringContaining("Remover os "));
  });

  it("Termos de Uso é nome de documento, sempre com maiúsculas", () => {
    const mentions = ALL_TEXTS.flatMap((text) => text.match(/termos de uso/gi) ?? []);

    expect(mentions.length).toBeGreaterThan(0);
    expect(mentions.filter((mention) => mention !== "Termos de Uso")).toEqual([]);
  });

  it("o elenco é todo feminino: as goblins, nunca os goblins", () => {
    expect(ALL_TEXTS.filter((text) => /\b(os|dos|nos|pelos|aos) goblins\b/i.test(text))).toEqual(
      [],
    );
  });
});
