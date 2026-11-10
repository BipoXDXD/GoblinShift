import { describe, expect, it } from "vitest";

import { selectCases } from "./selectCases.ts";

const CASES: readonly { readonly name: string }[] = [
  { name: "contraste: texto secundário no tema claro" },
  { name: "contraste: texto secundário no tema escuro" },
  { name: "CLS: aviso empurra o título no celular" },
];

describe("selectCases", () => {
  it("sem filtro seleciona todos os casos", () => {
    expect(selectCases(CASES, "")).toEqual({ found: true, cases: CASES });
  });

  it("seleciona só os casos cujo nome contém o filtro", () => {
    expect(selectCases(CASES, "tema escuro")).toEqual({ found: true, cases: [CASES[1]] });
  });

  it("filtro que só bate em parte do nome continua selecionando", () => {
    expect(selectCases(CASES, "CLS")).toEqual({ found: true, cases: [CASES[2]] });
  });

  it("filtro sem caso falha e sugere os nomes que dividem uma palavra com ele", () => {
    expect(selectCases(CASES, "Contraste do tema")).toEqual({
      found: false,
      message:
        'Nenhum caso de sabotagem contém "Contraste do tema". Os mais parecidos:\n' +
        "  contraste: texto secundário no tema claro\n" +
        "  contraste: texto secundário no tema escuro",
    });
  });

  it("filtro sem caso nem palavra em comum falha e lista todos os nomes", () => {
    expect(selectCases(CASES, "xyz")).toEqual({
      found: false,
      message:
        'Nenhum caso de sabotagem contém "xyz". Os casos disponíveis:\n' +
        "  contraste: texto secundário no tema claro\n" +
        "  contraste: texto secundário no tema escuro\n" +
        "  CLS: aviso empurra o título no celular",
    });
  });

  it("lista vazia de casos falha mesmo sem filtro", () => {
    expect(selectCases([], "")).toEqual({
      found: false,
      message: 'Nenhum caso de sabotagem contém "". Os casos disponíveis:\n',
    });
  });
});
