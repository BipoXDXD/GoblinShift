import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, problemResponse } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

function setup(path: string) {
  // Os créditos são públicos: abrem também sem sessão.
  const api = fakeApi({ "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED") });
  return renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
}

describe("rodapé", () => {
  it("toda tela mantém o acesso público aos créditos", async () => {
    setup("/");

    const footer = await screen.findByRole("contentinfo");
    expect(within(footer).getByRole("link", { name: "Créditos" })).toHaveAttribute(
      "href",
      "/creditos",
    );
  });
});

describe("/creditos", () => {
  it("abre com o foco no título e diz de onde vieram as goblins", async () => {
    setup("/creditos");

    await expectHeadingFocused("Créditos");
    const main = screen.getByRole("main");
    expect(main).toHaveTextContent("registro de licenças da arte");
    expect(main).toHaveTextContent("Blender");
  });

  it("apresenta as cinco goblins do elenco, cada uma com o nome", async () => {
    setup("/creditos");
    await expectHeadingFocused("Créditos");

    const cast = screen.getByRole("list", { name: "O elenco" });
    const names = within(cast)
      .getAllByRole("listitem")
      .map((item) => within(item).getByRole("figure").textContent);
    expect(names).toEqual(["Rúbia", "Fagulha", "Ferrugem", "Jade", "Nyx"]);
    // A foto repete o nome que a legenda já diz: é decorativa.
    for (const image of cast.querySelectorAll("img")) {
      expect(image).toHaveAttribute("alt", "");
    }
  });

  it("mostra o selo da oficina, a marca secundária, com texto alternativo e tamanho fixo", async () => {
    setup("/creditos");
    await expectHeadingFocused("Créditos");

    const section = screen.getByRole("region", { name: "A marca" });
    const seal = within(section).getByRole("img", { name: /^Selo da oficina GoblinShift/ });
    expect(seal).toHaveAttribute("width", "128");
    expect(seal).toHaveAttribute("height", "128");
    expect(seal).toHaveAttribute("loading", "lazy");
  });

  it("diz como a marca foi feita e que ela não é licenciada", async () => {
    setup("/creditos");
    await expectHeadingFocused("Créditos");

    const section = screen.getByRole("region", { name: "A marca" });
    expect(section).toHaveTextContent(
      "O emblema do cabeçalho e o selo da oficina mostram a Rúbia.",
    );
    expect(section).toHaveTextContent("versão própria em SVG");
    expect(section).toHaveTextContent(/não são licenciados/);
  });

  it("não tem violações do axe", async () => {
    const { container } = setup("/creditos");
    await expectHeadingFocused("Créditos");

    expect(await axeViolations(container)).toEqual([]);
  });
});
