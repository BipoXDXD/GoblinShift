import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { catalogHandler } from "../test/catalog";
import { fakeApi } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

function renderAt(path: string) {
  const api = fakeApi({ "GET /api/v1/catalog": catalogHandler() });
  return {
    ...renderRoutes({ routes: appRoutes(api.client), path, client: api.client }),
    user: userEvent.setup(),
  };
}

// As telas carregam por rota (lazy): o título só existe depois que o chunk da tela chega.
describe("rotas carregadas sob demanda", () => {
  it.each([
    ["/", "Arquivos em lote, limpos pelos goblins da oficina."],
    ["/bancada/csv-janitor", "Bancada: CSV Janitor"],
    ["/rota-que-nao-existe", "Página não encontrada"],
  ])("%s abre a tela e põe o foco no título", async (path, title) => {
    renderAt(path);

    await expectHeadingFocused(title);
  });

  it("ao navegar da Oficina para a Bancada, o foco vai ao título da tela nova", async () => {
    const { user } = renderAt("/");

    await user.click(await screen.findByRole("link", { name: /CSV Janitor/ }));

    // O título é trocado quando o catálogo chega; vale o foco no título que fica na tela.
    await expectHeadingFocused("Bancada: CSV Janitor");
  });
});
