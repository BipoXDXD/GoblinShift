import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

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

describe("título da aba e indexação", () => {
  it.each([
    ["/", "GoblinShift"],
    ["/bancada/csv-janitor", "Bancada · GoblinShift"],
    ["/historico", "Histórico · GoblinShift"],
    ["/entrar", "Entrar · GoblinShift"],
    ["/acesso", "Acesso · GoblinShift"],
    ["/termos", "Termos de Uso · GoblinShift"],
    ["/privacidade", "Aviso de Privacidade · GoblinShift"],
    ["/creditos", "Créditos · GoblinShift"],
    ["/termos/aceitar", "Aceite dos termos · GoblinShift"],
    ["/conta-excluida", "Exclusão pedida · GoblinShift"],
    ["/execucoes/0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f30", "Execução · GoblinShift"],
    ["/rota-que-nao-existe", "Página não encontrada · GoblinShift"],
  ])("%s tem o título %s", async (path, title) => {
    renderAt(path);

    await waitFor(() => {
      expect(document.title).toBe(title);
    });
  });

  it("a 404 pede aos buscadores para não indexar", async () => {
    renderAt("/rota-que-nao-existe");

    await expectHeadingFocused("Página não encontrada");
    expect(document.head.querySelector('meta[name="robots"]')).toHaveAttribute(
      "content",
      "noindex",
    );
  });

  it("uma tela que existe não leva o noindex", async () => {
    renderAt("/creditos");

    await waitFor(() => {
      expect(document.title).toBe("Créditos · GoblinShift");
    });
    expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
  });
});
