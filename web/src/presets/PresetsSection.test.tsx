import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, screenRequests } from "../test/fakeApi";
import { presetBody, PRESET_ID } from "../test/presets";
import { renderRoutes } from "../test/renderRoutes";

type Handler = (request: Request) => Response | Promise<Response> | null;

const CSRF = "csrf-da-sessao";
const DELETE_ROUTE = `DELETE /api/v1/presets/${PRESET_ID}`;

function setup(handlers: Readonly<Record<string, Handler>> = {}) {
  const api = fakeApi(
    {
      "GET /api/v1/runs": () => jsonResponse({ data: [], next_cursor: null }),
      "GET /api/v1/presets": () => jsonResponse({ data: [presetBody()] }),
      ...handlers,
    },
    { csrfToken: CSRF },
  );
  const view = renderRoutes({ routes: appRoutes(api.client), path: "/", client: api.client });
  return { ...view, api, user: userEvent.setup() };
}

function sent(requests: readonly Request[], route: string): Request[] {
  return screenRequests(requests).filter(
    (request) => `${request.method} ${new URL(request.url).pathname}` === route,
  );
}

async function presetsSection(): Promise<HTMLElement> {
  return screen.findByRole("region", { name: "Presets" });
}

describe("Oficina: presets", () => {
  it("lista os presets com a receita e o caminho para a Bancada pré-preenchida", async () => {
    setup();

    const section = await presetsSection();
    const list = await within(section).findByRole("list", { name: "Presets da conta" });
    const [item] = within(list).getAllByRole("listitem");
    expect(item).toHaveTextContent("Clientes do mês");
    expect(item).toHaveTextContent("CSV Janitor");
    expect(
      within(list).getByRole("link", { name: "Abrir na Bancada o preset Clientes do mês" }),
    ).toHaveAttribute("href", `/bancada/csv-janitor?preset=${PRESET_ID}`);
    expect(await axeViolations(section)).toEqual([]);
  });

  it("sem presets, diz como criar um", async () => {
    setup({ "GET /api/v1/presets": () => jsonResponse({ data: [] }) });

    const section = await presetsSection();
    expect(
      await within(section).findByRole("heading", { name: "Nenhum preset salvo" }),
    ).toBeVisible();
  });

  it("sem sessão, a seção não aparece e os presets nem são pedidos", async () => {
    const { api } = setup({
      "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED"),
    });

    await screen.findByRole("region", { name: "Execuções recentes" });
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Presets" })).toBeNull();
    });
    expect(sent(api.requests, "GET /api/v1/presets")).toEqual([]);
  });

  it("preset de receita que a interface não conhece aparece sem link", async () => {
    setup({
      "GET /api/v1/presets": () =>
        jsonResponse({ data: [presetBody({ recipe_id: "receita-futura", name: "Futuro" })] }),
    });

    const section = await presetsSection();
    expect(await within(section).findByText("Futuro")).toBeVisible();
    expect(within(section).queryByRole("link", { name: /Abrir na Bancada/ })).toBeNull();
  });

  it("excluir pede confirmação, manda o If-Match da revisão e anuncia", async () => {
    let deleted = false;
    const { api, user } = setup({
      "GET /api/v1/presets": () =>
        jsonResponse({ data: deleted ? [] : [presetBody({ revision: 4 })] }),
      [DELETE_ROUTE]: () => {
        deleted = true;
        return new Response(null, { status: 204 });
      },
    });

    const section = await presetsSection();
    await user.click(
      await within(section).findByRole("button", { name: "Excluir o preset Clientes do mês" }),
    );
    const dialog = screen.getByRole("dialog", { name: "Excluir o preset Clientes do mês?" });
    expect(within(dialog).getByRole("button", { name: "Cancelar" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "Excluir preset" }));

    expect(await screen.findByText("Preset Clientes do mês excluído.")).toBeInTheDocument();
    const [request] = sent(api.requests, DELETE_ROUTE);
    expect(request?.headers.get("If-Match")).toBe('"4"');
    expect(request?.headers.get("X-CSRF-Token")).toBe(CSRF);
    expect(
      await within(section).findByRole("heading", { name: "Nenhum preset salvo" }),
    ).toBeVisible();
  });

  it("cancelar a exclusão não chama a API", async () => {
    const { api, user } = setup();

    const section = await presetsSection();
    await user.click(
      await within(section).findByRole("button", { name: "Excluir o preset Clientes do mês" }),
    );
    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(sent(api.requests, DELETE_ROUTE)).toEqual([]);
  });

  it("revisão vencida (412) avisa e relê a lista", async () => {
    const { api, user } = setup({
      [DELETE_ROUTE]: () => problemResponse(412, "PRECONDITION_FAILED"),
    });

    const section = await presetsSection();
    await user.click(
      await within(section).findByRole("button", { name: "Excluir o preset Clientes do mês" }),
    );
    await user.click(screen.getByRole("button", { name: "Excluir preset" }));

    expect(
      await screen.findByText(/O preset mudou em outra aba ou dispositivo/),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(sent(api.requests, "GET /api/v1/presets")).toHaveLength(2);
    });
  });
});
