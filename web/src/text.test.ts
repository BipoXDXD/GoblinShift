import { describe, expect, it } from "vitest";

import { TEXT } from "./text";

// Percorre o módulo inteiro. Dos textos com variável entra o código-fonte da função, que traz os
// trechos fixos do template; os argumentos não importam para o vocabulário.
type TextTree =
  | string
  | ((...args: never[]) => string)
  | readonly TextTree[]
  | { readonly [key: string]: TextTree };

interface CollectedText {
  readonly fromFunction: boolean;
  readonly text: string;
}

function collectTexts(value: TextTree): CollectedText[] {
  if (typeof value === "string") {
    return [{ fromFunction: false, text: value }];
  }
  if (typeof value === "function") {
    return [{ fromFunction: true, text: value.toString() }];
  }
  return Object.values(value).flatMap(collectTexts);
}

const COLLECTED = collectTexts(TEXT);
const ALL_TEXTS = COLLECTED.map(({ text }) => text);

// Artigo, contração ou numeral masculino antes de "goblin(s)": "os goblins", "do goblin", "um goblin".
const MASCULINE_GOBLIN =
  /\b(o|os|do|dos|no|nos|pelo|pelos|ao|aos|um|uns|dum|duns|num|nuns) goblins?\b/i;

describe("vocabulário da interface", () => {
  it("percorre também os textos que são funções", () => {
    expect(COLLECTED.some(({ fromFunction }) => fromFunction)).toBe(true);
  });

  it("Termos de Uso é nome de documento, sempre com maiúsculas", () => {
    const mentions = ALL_TEXTS.flatMap((text) => text.match(/termos de uso/gi) ?? []);

    expect(mentions.length).toBeGreaterThan(0);
    expect(mentions.filter((mention) => mention !== "Termos de Uso")).toEqual([]);
  });

  it("o elenco é todo feminino: as goblins, nunca o goblin nem os goblins", () => {
    expect(ALL_TEXTS.filter((text) => MASCULINE_GOBLIN.test(text))).toEqual([]);
  });
});
