import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import privacy from "../../../docs/legal/aviso-de-privacidade.md?raw";
import terms from "../../../docs/legal/termos-de-uso.md?raw";
import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, problemResponse } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

// Material interno da revisão (pendências, ids do levantamento, notas ao revisor, fontes e as
// instruções de convenção) mora em docs/legal/notas-internas.md, que nada importa.
const INTERNAL_MARKERS = [
  /Pendências/i,
  /\bJ-\d/,
  /notas internas/i,
  /Fontes internas/i,
  /Nota para a revisão/i,
  /Divergências entre plano e código/i,
  /não publicar sem revisão jurídica/i,
  /Esta seção sai do texto final/i,
  /Trechos entre/i,
  /Notas marcadas como/i,
];

function setup(path: string) {
  // Os textos são públicos: abrem também sem sessão.
  const api = fakeApi({ "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED") });
  return renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
}

describe.each([
  ["/termos", "Termos de Uso do GoblinShift", terms],
  ["/privacidade", "Aviso de Privacidade do GoblinShift", privacy],
])("%s", (path, title, source) => {
  it("o título do arquivo é o h1 da tela, sem aviso de rascunho", async () => {
    setup(path);

    await expectHeadingFocused(title);
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("cada seção ## do arquivo vira um h2", async () => {
    setup(path);
    await expectHeadingFocused(title);

    const expected = [...source.matchAll(/^## (.+)$/gm)].map(([, heading]) =>
      (heading ?? "").replaceAll("**", ""),
    );
    const shown = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    expect(expected.length).toBeGreaterThan(5);
    expect(shown).toEqual(expected);
  });

  it("o arquivo embutido no build não traz material interno da revisão", () => {
    for (const marker of INTERNAL_MARKERS) {
      expect(source).not.toMatch(marker);
    }
  });

  it("a página não mostra material interno nem marcações a definir", async () => {
    setup(path);
    await expectHeadingFocused(title);

    const shown = screen.getByRole("article").textContent;
    for (const marker of INTERNAL_MARKERS) {
      expect(shown).not.toMatch(marker);
    }
    expect(shown).not.toContain("[[A DEFINIR");
  });

  it("não tem violações do axe", async () => {
    const { container } = setup(path);
    await expectHeadingFocused(title);

    expect(await axeViolations(container)).toEqual([]);
  });

  it("o sumário tem um link por h2, na ordem e com o mesmo texto", async () => {
    setup(path);
    await expectHeadingFocused(title);

    const sections = screen
      .getAllByRole("heading", { level: 2 })
      .map((heading) => heading.textContent);
    const outline = screen.getByRole("navigation", { name: "Sumário" });
    const entries = within(outline)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(entries).toEqual(sections);
  });

  it("cada link do sumário leva ao h2 da seção", async () => {
    setup(path);
    await expectHeadingFocused(title);

    const outline = screen.getByRole("navigation", { name: "Sumário" });
    for (const link of within(outline).getAllByRole("link")) {
      const href = link.getAttribute("href") ?? "";
      expect(href).toMatch(/^#secao-[a-z0-9-]+$/);
      const target = document.getElementById(href.slice(1));
      expect(target?.tagName).toBe("H2");
      expect(target).toHaveTextContent(link.textContent);
    }
  });
});

describe("e-mails de contato", () => {
  it.each([
    ["/privacidade", "Aviso de Privacidade do GoblinShift", "privacidade@goblinshift.dev"],
    ["/termos", "Termos de Uso do GoblinShift", "privacidade@goblinshift.dev"],
    ["/termos", "Termos de Uso do GoblinShift", "contato@goblinshift.dev"],
    ["/termos", "Termos de Uso do GoblinShift", "abuso@goblinshift.dev"],
  ])("%s: %s abre o e-mail", async (path, title, address) => {
    setup(path);
    await expectHeadingFocused(title);

    const links = within(screen.getByRole("article")).getAllByRole("link", { name: address });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", `mailto:${address}`);
    }
  });
});

describe("links entre os textos", () => {
  it("o link dos Termos para o aviso abre a rota da interface", async () => {
    setup("/termos");
    await expectHeadingFocused("Termos de Uso do GoblinShift");

    const links = within(screen.getByRole("article")).getAllByRole("link", {
      name: "Aviso de Privacidade",
    });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", "/privacidade");
    }
  });
});

describe("links para fora da interface", () => {
  it.each([
    "https://goblinshift.dev/.well-known/security.txt",
    "https://github.com/BipoXDXD/GoblinShift/blob/main/LICENSE",
    "https://github.com/BipoXDXD/GoblinShift/blob/main/docs/art/LICENSE-ART.md",
  ])("os Termos levam a %s", async (href) => {
    setup("/termos");
    await expectHeadingFocused("Termos de Uso do GoblinShift");

    const links = within(screen.getByRole("article")).getAllByRole("link", { name: href });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link).toHaveAttribute("href", href);
    }
  });
});
