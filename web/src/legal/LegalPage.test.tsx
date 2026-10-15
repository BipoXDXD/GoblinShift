import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import privacy from "../../../docs/legal/aviso-de-privacidade.md?raw";
import terms from "../../../docs/legal/termos-de-uso.md?raw";
import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, problemResponse } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

function setup(path: string) {
  // Os textos são públicos: abrem também sem sessão.
  const api = fakeApi({ "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED") });
  return renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
}

describe.each([
  ["/termos", "Termos de Uso do GoblinShift", terms],
  ["/privacidade", "Aviso de Privacidade do GoblinShift", privacy],
])("%s", (path, title, source) => {
  it("o título do arquivo é o h1 da tela, com o aviso de rascunho logo abaixo", async () => {
    setup(path);

    await expectHeadingFocused(title);
    expect(screen.getByRole("complementary", { name: "Rascunho" })).toHaveTextContent(
      "Este texto ainda não passou por revisão jurídica",
    );
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

  it("não tem violações do axe", async () => {
    const { container } = setup(path);
    await expectHeadingFocused(title);

    expect(await axeViolations(container)).toEqual([]);
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
