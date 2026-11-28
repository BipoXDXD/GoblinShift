import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { fakeApi, problemResponse } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

await preloadRoutes("/acessibilidade");

function setup(path: string) {
  // A declaração é pública: abre também sem sessão.
  const api = fakeApi(
    { "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED") },
    { csrfToken: null },
  );
  return renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
}

describe("/acessibilidade", () => {
  it("abre sem sessão com o título da declaração e o alvo WCAG 2.2 AA", async () => {
    setup("/acessibilidade");

    await expectHeadingFocused("Declaração de Acessibilidade do GoblinShift");
    expect(screen.getByRole("main")).toHaveTextContent("parcialmente conforme");
  });

  it("o link do rodapé leva a ela", async () => {
    setup("/");

    const footer = await screen.findByRole("contentinfo");
    expect(within(footer).getByRole("link", { name: "Acessibilidade" })).toHaveAttribute(
      "href",
      "/acessibilidade",
    );
  });
});
