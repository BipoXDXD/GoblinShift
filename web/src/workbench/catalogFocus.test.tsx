import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, onTestFinished } from "vitest";

import { CATALOG_BODY } from "../test/catalogBody";
import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";

await preloadRoutes("/bancada/csv-janitor");

/** Abre a Bancada com o GET /catalog em voo; `release` entrega a resposta quando o teste quiser. */
function renderWithHeldCatalog() {
  let release: () => void = () => undefined;
  const held = new Promise<Response>((resolve) => {
    release = () => {
      resolve(jsonResponse(CATALOG_BODY, 200, { ETag: '"catalogo-de-teste"' }));
    };
  });
  const api = fakeApi({ "GET /api/v1/catalog": () => held });
  const view = renderRoutes({
    routes: appRoutes(api.client),
    path: "/bancada/csv-janitor",
    client: api.client,
  });
  return { ...view, release };
}

/** Cada vez que um `h1` recebe o foco, venha ele do mesmo elemento ou de um que remontou. */
function recordHeadingFocus(): { readonly count: () => number } {
  let focused = 0;
  const onFocusIn = (event: FocusEvent): void => {
    if (event.target instanceof HTMLHeadingElement) {
      focused += 1;
    }
  };
  document.addEventListener("focusin", onFocusIn);
  onTestFinished(() => {
    document.removeEventListener("focusin", onFocusIn);
  });
  return { count: () => focused };
}

describe("Bancada enquanto o catálogo carrega", () => {
  it("o catálogo que chega não tira o foco de quem já está no seletor de tema", async () => {
    const { release } = renderWithHeldCatalog();
    await screen.findByText("Carregando a receita.");
    await userEvent.click(screen.getByText("Tema"));
    const system = screen.getByRole("radio", { name: "Sistema" });
    act(() => {
      system.focus();
    });

    release();
    await screen.findByLabelText("Escolher arquivos");

    expect(system).toHaveFocus();
    expect(
      screen.getByRole("group", { name: "Tema da interface" }).closest("details"),
    ).toHaveAttribute("open");
    expect(screen.getByRole("heading", { level: 1 })).not.toHaveFocus();
  });

  it("ao entrar na página o título recebe o foco uma única vez, com o catálogo já na tela", async () => {
    const headings = recordHeadingFocus();
    const { release } = renderWithHeldCatalog();
    await expectHeadingFocused("Bancada: CSV Janitor");

    release();
    await screen.findByLabelText("Escolher arquivos");

    await expectHeadingFocused("Bancada: CSV Janitor");
    expect(headings.count()).toBe(1);
  });
});
