import { describe, expect, it } from "vitest";

import { parseInline, parseMarkdown } from "./markdown";

const text = (value: string) => ({ kind: "text", text: value }) as const;

describe("parseInline", () => {
  it("texto puro é um trecho só", () => {
    expect(parseInline("só texto")).toEqual([text("só texto")]);
  });

  it("vazio não gera trecho", () => {
    expect(parseInline("")).toEqual([]);
  });

  it("negrito, código e link no meio do texto", () => {
    expect(parseInline("a **b** c `d` e [Aviso](aviso-de-privacidade.md) f")).toEqual([
      text("a "),
      { kind: "strong", children: [text("b")] },
      text(" c "),
      { kind: "code", text: "d" },
      text(" e "),
      { kind: "link", href: "aviso-de-privacidade.md", children: [text("Aviso")] },
      text(" f"),
    ]);
  });

  it("código dentro do negrito", () => {
    expect(parseInline("**sem a `query`**")).toEqual([
      { kind: "strong", children: [text("sem a "), { kind: "code", text: "query" }] },
    ]);
  });

  it("sintaxe incompleta e HTML ficam como texto literal", () => {
    expect(parseInline("**aberto e <domínio> e [x](")).toEqual([
      text("**aberto e <domínio> e [x]("),
    ]);
  });
});

describe("parseMarkdown", () => {
  it("vazio não tem blocos", () => {
    expect(parseMarkdown("")).toEqual([]);
    expect(parseMarkdown("\n\n  \n")).toEqual([]);
  });

  it("títulos de 1 a 4 níveis; 5 ou mais viram parágrafo", () => {
    expect(parseMarkdown("# Um\n## Dois\n### Três\n#### Quatro\n##### Cinco")).toEqual([
      { kind: "heading", level: 1, inlines: [text("Um")] },
      { kind: "heading", level: 2, inlines: [text("Dois")] },
      { kind: "heading", level: 3, inlines: [text("Três")] },
      { kind: "heading", level: 4, inlines: [text("Quatro")] },
      { kind: "paragraph", inlines: [text("##### Cinco")] },
    ]);
  });

  it("linhas seguidas são um parágrafo; linha em branco separa", () => {
    expect(parseMarkdown("um\ndois\n\ntrês")).toEqual([
      { kind: "paragraph", inlines: [text("um dois")] },
      { kind: "paragraph", inlines: [text("três")] },
    ]);
  });

  it("citação com parágrafos dentro", () => {
    expect(parseMarkdown("> **RASCUNHO**\n>\n> outro")).toEqual([
      {
        kind: "quote",
        blocks: [
          { kind: "paragraph", inlines: [{ kind: "strong", children: [text("RASCUNHO")] }] },
          { kind: "paragraph", inlines: [text("outro")] },
        ],
      },
    ]);
  });

  it("lista com item continuado na linha recuada", () => {
    expect(parseMarkdown("- um\n- dois\n  continua\n\nfim")).toEqual([
      { kind: "list", ordered: false, items: [[text("um")], [text("dois continua")]] },
      { kind: "paragraph", inlines: [text("fim")] },
    ]);
  });

  it("lista numerada, também com linha em branco entre itens", () => {
    expect(parseMarkdown("1. um\n\n2. dois")).toEqual([
      { kind: "list", ordered: true, items: [[text("um")], [text("dois")]] },
    ]);
  });

  it("parágrafo termina quando começa uma lista", () => {
    expect(parseMarkdown("entre eles:\n- um")).toEqual([
      { kind: "paragraph", inlines: [text("entre eles:")] },
      { kind: "list", ordered: false, items: [[text("um")]] },
    ]);
  });

  it("tabela com cabeçalho e linhas", () => {
    expect(parseMarkdown("| Dado | Prazo |\n|---|---|\n| Conta | **24 h** |")).toEqual([
      {
        kind: "table",
        header: [[text("Dado")], [text("Prazo")]],
        rows: [[[text("Conta")], [{ kind: "strong", children: [text("24 h")] }]]],
      },
    ]);
  });

  it("linha com barra sem separador é parágrafo, não tabela", () => {
    expect(parseMarkdown("| sozinha |")).toEqual([
      { kind: "paragraph", inlines: [text("| sozinha |")] },
    ]);
  });

  it("régua horizontal", () => {
    expect(parseMarkdown("antes\n\n---\n\ndepois")).toEqual([
      { kind: "paragraph", inlines: [text("antes")] },
      { kind: "rule" },
      { kind: "paragraph", inlines: [text("depois")] },
    ]);
  });
});
