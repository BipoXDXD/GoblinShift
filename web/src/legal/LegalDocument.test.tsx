import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { LegalDocument } from "./LegalDocument";
import { parseMarkdown } from "./markdown";

function renderDocument(source: string) {
  return render(
    <MemoryRouter>
      <LegalDocument blocks={parseMarkdown(source)} />
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

  it("texto sem e-mail fica igual", () => {
    renderDocument("Nada de arroba aqui.");

    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("Nada de arroba aqui.")).toBeInTheDocument();
  });
});
