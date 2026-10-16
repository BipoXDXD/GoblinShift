import { describe, expect, it } from "vitest";

import { MAX_SAMPLE_ROWS, parseCsvSample } from "./csvSample";

describe("parseCsvSample", () => {
  it("separa cabeçalho e registros", () => {
    expect(parseCsvSample("nome,email\nAna,ana@example.com\nBeto,beto@example.com\n")).toEqual({
      header: ["nome", "email"],
      rows: [
        ["Ana", "ana@example.com"],
        ["Beto", "beto@example.com"],
      ],
    });
  });

  it("só o cabeçalho é uma amostra sem registros", () => {
    expect(parseCsvSample("nome,email\n")).toEqual({ header: ["nome", "email"], rows: [] });
  });

  it("aceita CRLF e a última linha sem quebra", () => {
    expect(parseCsvSample("a,b\r\n1,2")).toEqual({ header: ["a", "b"], rows: [["1", "2"]] });
  });

  it("campo entre aspas guarda vírgula, aspas dobradas e quebra de linha", () => {
    expect(parseCsvSample('a,b\n"x, y","diz ""oi""\nfim"\n')).toEqual({
      header: ["a", "b"],
      rows: [["x, y", 'diz "oi"\nfim']],
    });
  });

  it("mantém o tab da guarda de fórmula como parte do valor", () => {
    expect(parseCsvSample("a\n\"\t=cmd|' /C calc'!A0\"\n")?.rows).toEqual([
      ["\t=cmd|' /C calc'!A0"],
    ]);
  });

  it("campos vazios continuam no lugar", () => {
    expect(parseCsvSample('a,b,c\n,,\n""\n')?.rows).toEqual([["", "", ""], [""]]);
  });

  it("corta em MAX_SAMPLE_ROWS registros", () => {
    const lines = ["n", ...Array.from({ length: MAX_SAMPLE_ROWS + 5 }, (_, i) => String(i))];

    const sample = parseCsvSample(lines.join("\n"));

    expect(sample?.rows).toHaveLength(MAX_SAMPLE_ROWS);
    expect(sample?.rows.at(-1)).toEqual([String(MAX_SAMPLE_ROWS - 1)]);
  });

  it.each([
    ["vazio", ""],
    ["aspas sem fechar", 'a\n"aberto\n'],
    ["texto depois das aspas", 'a\n"x"y\n'],
    ["aspas no meio de campo sem aspas", 'a\nx"y\n'],
  ])("recusa %s", (_, text) => {
    expect(parseCsvSample(text)).toBeNull();
  });
});
