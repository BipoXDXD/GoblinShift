import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, onTestFinished } from "vitest";

import { CATALOG_BODY } from "../test/catalogBody";
import { appRoutes } from "../test/appRoutes";
import { withVersion } from "../test/catalog";
import { fakeApi, jsonResponse, problemResponse } from "../test/fakeApi";
import { expectHeadingFocused } from "../test/focus";
import { renderRoutes } from "../test/renderRoutes";
import { TEXT } from "../text";

/**
 * Abre a Bancada com o GET /catalog em voo; `release` entrega a resposta quando o teste quiser,
 * o catálogo de exemplo ou, se o teste passar `answer`, a resposta que ele escolher.
 */
function renderWithHeldCatalog(
  answer: () => Response = () => jsonResponse(CATALOG_BODY, 200, { ETag: '"catalogo-de-teste"' }),
) {
  let release: () => void = () => undefined;
  const held = new Promise<Response>((resolve) => {
    release = () => {
      resolve(answer());
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

describe("Bancada quando o catálogo chega com falha", () => {
  it("o título recebe o foco uma única vez, antes e depois da falha do catálogo", async () => {
    const headings = recordHeadingFocus();
    const { release } = renderWithHeldCatalog(() => problemResponse(503, "SERVICE_UNAVAILABLE"));
    await expectHeadingFocused("Bancada: CSV Janitor");

    release();
    expect(await screen.findByRole("alert")).toHaveTextContent(/O serviço está indisponível/);

    await expectHeadingFocused("Bancada: CSV Janitor");
    expect(headings.count()).toBe(1);
  });

  it("com a falha do catálogo na tela há um único h1, o da Bancada", async () => {
    const { release } = renderWithHeldCatalog(() => problemResponse(503, "SERVICE_UNAVAILABLE"));
    await screen.findByText("Carregando a receita.");

    release();
    await screen.findByRole("alert");

    const titles = screen.getAllByRole("heading", { level: 1 });
    expect(titles).toHaveLength(1);
    expect(titles[0]).toHaveTextContent("Bancada: CSV Janitor");
  });
});

describe("Bancada com a receita indisponível no catálogo", () => {
  const withoutRecipeForm = (): Response =>
    jsonResponse(withVersion("csv-janitor", null), 200, { ETag: '"catalogo-de-teste"' });

  it("o título recebe o foco uma única vez, antes e depois do aviso de receita indisponível", async () => {
    const headings = recordHeadingFocus();
    const { release } = renderWithHeldCatalog(withoutRecipeForm);
    await expectHeadingFocused("Bancada: CSV Janitor");

    release();
    expect(await screen.findByRole("alert")).toHaveTextContent(TEXT.workbench.recipeUnavailable);

    await expectHeadingFocused("Bancada: CSV Janitor");
    expect(headings.count()).toBe(1);
  });

  it("com o aviso de receita indisponível na tela há um único h1, o da Bancada", async () => {
    const { release } = renderWithHeldCatalog(withoutRecipeForm);
    await screen.findByText("Carregando a receita.");

    release();
    await screen.findByRole("alert");

    const titles = screen.getAllByRole("heading", { level: 1 });
    expect(titles).toHaveLength(1);
    expect(titles[0]).toHaveTextContent("Bancada: CSV Janitor");
  });
});
