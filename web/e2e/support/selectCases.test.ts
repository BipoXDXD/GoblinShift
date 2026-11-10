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

  it("filtro que bate em vários nomes seleciona todos, na ordem da lista", () => {
    expect(selectCases(CASES, "contraste")).toEqual({ found: true, cases: [CASES[0], CASES[1]] });
  });

  it("a seleção diferencia caixa, mas a sugestão não: cls aponta o caso CLS", () => {
    expect(selectCases(CASES, "cls")).toEqual({
      found: false,
      message:
        'Nenhum caso de sabotagem contém "cls". Casos com uma palavra em comum:\n' +
        "  CLS: aviso empurra o título no celular",
    });
  });

  it("basta uma palavra do filtro em comum para sugerir o nome", () => {
    expect(selectCases(CASES, "contraste azul")).toEqual({
      found: false,
      message:
        'Nenhum caso de sabotagem contém "contraste azul". Casos com uma palavra em comum:\n' +
        "  contraste: texto secundário no tema claro\n" +
        "  contraste: texto secundário no tema escuro",
    });
  });

  it("palavra de 3 letras conta para a sugestão", () => {
    expect(selectCases(CASES, "CLS!")).toEqual({
      found: false,
      message:
        'Nenhum caso de sabotagem contém "CLS!". Casos com uma palavra em comum:\n' +
        "  CLS: aviso empurra o título no celular",
    });
  });

  it("palavra de 2 letras não conta para a sugestão", () => {
    expect(selectCases(CASES, "no xyz")).toEqual({
      found: false,
      message:
        'Nenhum caso de sabotagem contém "no xyz". Os casos disponíveis:\n' +
        "  contraste: texto secundário no tema claro\n" +
        "  contraste: texto secundário no tema escuro\n" +
        "  CLS: aviso empurra o título no celular",
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
