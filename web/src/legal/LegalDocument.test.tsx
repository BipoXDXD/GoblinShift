import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { LegalDocument, sectionsOf } from "./LegalDocument";
import { parseMarkdown } from "./markdown";

function renderDocument(source: string) {
  const blocks = parseMarkdown(source);
  return render(
    <MemoryRouter>
      <LegalDocument blocks={blocks} sections={sectionsOf(blocks)} />
    </MemoryRouter>,
  );
}

describe("sumário", () => {
  it("texto sem títulos não ganha sumário", () => {
    renderDocument("Um parágrafo só.\n\nOutro parágrafo.");

    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("texto curto, com duas seções, não ganha sumário", () => {
    renderDocument("## 1. Uma\n\ntexto\n\n## 2. Outra\n\ntexto");

    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("a partir de três seções o sumário aparece, só com os h2", () => {
    renderDocument("## 1. Uma\n\n### 1.1. Sub\n\n## 2. Outra\n\n## Histórico de versões");

    const links = screen.getAllByRole("link");
    expect(screen.getByRole("navigation", { name: "Sumário" })).toBeInTheDocument();
    expect(links.map((link) => link.textContent)).toEqual([
      "1. Uma",
      "2. Outra",
      "Histórico de versões",
    ]);
  });

  it("a âncora vem do texto do título, sem acento nem pontuação", () => {
    renderDocument("## 1. Uma\n\n## 2. O que **não** coletamos\n\n## Histórico de versões");

    expect(screen.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "#secao-1-uma",
      "#secao-2-o-que-nao-coletamos",
      "#secao-historico-de-versoes",
    ]);
  });

  it("títulos repetidos ganham âncoras distintas", () => {
    renderDocument("## Notas\n\n## Notas\n\n## Notas");

    expect(screen.getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "#secao-notas",
      "#secao-notas-2",
      "#secao-notas-3",
    ]);
    expect(document.getElementById("secao-notas-3")?.tagName).toBe("H2");
  });

  it("sufixo de repetido não colide com outro título: ids e links únicos", () => {
    renderDocument("## Notas\n\n## Notas\n\n## Notas 2");

    const hrefs = screen.getAllByRole("link").map((link) => link.getAttribute("href"));
    expect(hrefs).toHaveLength(3);
    expect(new Set(hrefs).size).toBe(3);
    const ids = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.id);
    expect(new Set(ids).size).toBe(3);
    expect(hrefs).toEqual(ids.map((id) => `#${id}`));
  });

  it("o sumário vem depois do preâmbulo, logo antes do primeiro h2", () => {
    renderDocument("- **Versão:** v1\n\nAbertura.\n\n## 1. Uma\n\n## 2. Duas\n\n## 3. Três");

    const outline = screen.getByRole("navigation", { name: "Sumário" });
    const opening = screen.getByText("Abertura.");
    const [first] = screen.getAllByRole("heading", { level: 2 });
    expect(opening.compareDocumentPosition(outline)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(outline.nextElementSibling).toBe(first);
  });
});

describe("e-mails no texto", () => {
  it("endereço completo vira mailto e o ponto final fica fora do link", () => {
    renderDocument("Escreva para privacidade@goblinshift.dev.");

    const link = screen.getByRole("link", { name: "privacidade@goblinshift.dev" });
    expect(link).toHaveAttribute("href", "mailto:privacidade@goblinshift.dev");
    expect(link.parentElement).toHaveTextContent("Escreva para privacidade@goblinshift.dev.");
  });

  it("prefixo sem domínio, como contato@, continua texto", () => {
    renderDocument("Nos endereços contato@, abuso@ ou privacidade@goblinshift.dev, guardamos.");

    expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual([
      "privacidade@goblinshift.dev",
    ]);
  });

  it("e-mail dentro do texto de um link não vira link aninhado", () => {
    renderDocument("Veja [abuso@goblinshift.dev](https://goblinshift.dev/contato).");

    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "https://goblinshift.dev/contato");
  });

  it.each([
    ["parâmetro ?cc=", "Escreva para x@y.com?cc=a@b.com&body=oi agora."],
    ["parâmetros ?=&", "Escreva para x@y.com?=&z=1 agora."],
    ["esquema javascript:", "Clique em javascript:alert(1)@x.com agora."],
  ])("%s não entra no href", (_case, source) => {
    renderDocument(source);

    for (const link of screen.queryAllByRole("link")) {
      expect(link.getAttribute("href")).toMatch(/^mailto:[\w.+-]+@[\w-]+(?:\.[\w-]+)+$/);
    }
    expect(screen.queryByRole("link", { name: /javascript|\?|&|=/ })).not.toBeInTheDocument();
  });

  it("texto sem e-mail fica igual", () => {
    renderDocument("Nada de arroba aqui.");

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("Nada de arroba aqui.")).toBeInTheDocument();
  });
});

describe("nome das tabelas", () => {
  it("a tabela leva o título mais próximo acima dela, também de subseção", () => {
    renderDocument(
      "## 3. Dados\n\n### 3.1. Da conta\n\n| Dado | Prazo |\n| --- | --- |\n| e-mail | 1 ano |",
    );

    expect(screen.getByRole("region", { name: "3.1. Da conta" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "3.1. Da conta" })).toBeInTheDocument();
  });

  it("sem título antes, a tabela leva o nome das colunas", () => {
    renderDocument("| Dado | Prazo |\n| --- | --- |\n| e-mail | 1 ano |");

    expect(screen.getByRole("region", { name: "Tabela: Dado, Prazo" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Tabela: Dado, Prazo" })).toBeInTheDocument();
  });

  it("sem título nem colunas com nome, a tabela ainda tem nome", () => {
    renderDocument("| | |\n| --- | --- |\n| a | b |");

    expect(screen.getByRole("region", { name: "Tabela" })).toBeInTheDocument();
  });
});
