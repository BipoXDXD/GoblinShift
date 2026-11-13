import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, screenRequests } from "../test/fakeApi";
import { presetBody, PRESET_ID } from "../test/presets";
import { escapeIsBlocked, forceClose, heldResponse } from "../test/dialog";
import { renderRoutes } from "../test/renderRoutes";
import { PRESETS_QUERY_KEY } from "./api";

await preloadRoutes("/", "/bancada/csv-janitor");

type Handler = (request: Request) => Response | Promise<Response> | null;

const CSRF = "csrf-da-sessao";
const DELETE_ROUTE = `DELETE /api/v1/presets/${PRESET_ID}`;
const SECOND_PRESET_ID = "7a8b9c0d-1e2f-4a3b-8c4d-5e6f7a8b9c0d";

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

  // O <dialog> devolveria o foco ao Excluir, que some com o preset (WCAG 2.4.3).
  it.each([
    { caso: "excluído (204)", answer: () => new Response(null, { status: 204 }) },
    { caso: "de revisão vencida (412)", answer: () => problemResponse(412, "PRECONDITION_FAILED") },
  ])("preset $caso: o foco vai para o título da seção", async ({ answer }) => {
    const { user } = setup({ [DELETE_ROUTE]: answer });

    const section = await presetsSection();
    await user.click(
      await within(section).findByRole("button", { name: "Excluir o preset Clientes do mês" }),
    );
    await user.click(screen.getByRole("button", { name: "Excluir preset" }));

    await waitFor(() => {
      expect(within(section).getByRole("heading", { level: 2, name: "Presets" })).toHaveFocus();
    });
  });

  // Falha genérica (5xx, timeout) com a exclusão aplicada no servidor: o Excluir some na releitura
  // e o foco cairia no <body> (WCAG 2.4.3).
  it("500 com a lista já sem o preset: o foco vai para o título e a falha aparece", async () => {
    let settled = false;
    const { user } = setup({
      "GET /api/v1/presets": () => jsonResponse({ data: settled ? [] : [presetBody()] }),
      [DELETE_ROUTE]: () => {
        settled = true;
        return problemResponse(500, "INTERNAL_ERROR");
      },
    });

    const section = await presetsSection();
    await user.click(
      await within(section).findByRole("button", { name: "Excluir o preset Clientes do mês" }),
    );
    await user.click(screen.getByRole("button", { name: "Excluir preset" }));

    expect(await within(section).findByRole("alert")).toBeVisible();
    await waitFor(() => {
      expect(within(section).getByRole("heading", { level: 2, name: "Presets" })).toHaveFocus();
    });
  });

  it("500 com o preset ainda na lista: o foco volta ao Excluir", async () => {
    const { user } = setup({ [DELETE_ROUTE]: () => problemResponse(500, "INTERNAL_ERROR") });

    const section = await presetsSection();
    await user.click(
      await within(section).findByRole("button", { name: "Excluir o preset Clientes do mês" }),
    );
    await user.click(screen.getByRole("button", { name: "Excluir preset" }));

    expect(await within(section).findByRole("alert")).toBeVisible();
    expect(
      within(section).getByRole("button", { name: "Excluir o preset Clientes do mês" }),
    ).toHaveFocus();
  });

  // WCAG 3.2: o destino do foco é decidido uma vez, na resposta. Uma releitura posterior da lista
  // (a pessoa volta de outra aba) não pode puxar o foco para o título sem ação dela.
  it("500 com o preset na lista: a releitura posterior não move o foco que a pessoa tirou", async () => {
    let updatedAt = "2026-10-01T12:00:00Z";
    const { queryClient, user } = setup({
      "GET /api/v1/presets": () => jsonResponse({ data: [presetBody({ updated_at: updatedAt })] }),
      [DELETE_ROUTE]: () => problemResponse(500, "INTERNAL_ERROR"),
    });
    const section = await presetsSection();
    await user.click(
      await within(section).findByRole("button", { name: "Excluir o preset Clientes do mês" }),
    );
    await user.click(screen.getByRole("button", { name: "Excluir preset" }));
    expect(await within(section).findByRole("alert")).toBeVisible();
    const remove = within(section).getByRole("button", {
      name: "Excluir o preset Clientes do mês",
    });
    await waitFor(() => {
      expect(remove).toHaveFocus();
    });
    const before = within(section).getByText(/Atualizado em/).textContent;

    remove.blur();
    updatedAt = "2026-10-20T15:30:00Z";
    await queryClient.invalidateQueries({ queryKey: PRESETS_QUERY_KEY });

    await waitFor(() => {
      expect(within(section).getByText(/Atualizado em/).textContent).not.toBe(before);
    });
    expect(document.body).toHaveFocus();
  });

  // O destino da exclusão anterior não vale para o próximo diálogo (WCAG 2.4.3).
  it("depois de excluir um preset, cancelar a exclusão de outro devolve o foco ao Excluir dele", async () => {
    let deleted = false;
    const other = presetBody({ id: SECOND_PRESET_ID, name: "Fornecedores" });
    const { user } = setup({
      "GET /api/v1/presets": () =>
        jsonResponse({ data: deleted ? [other] : [presetBody(), other] }),
      [DELETE_ROUTE]: () => {
        deleted = true;
        return new Response(null, { status: 204 });
      },
    });
    const section = await presetsSection();
    await user.click(
      await within(section).findByRole("button", { name: "Excluir o preset Clientes do mês" }),
    );
    await user.click(screen.getByRole("button", { name: "Excluir preset" }));
    await waitFor(() => {
      expect(within(section).getByRole("heading", { level: 2, name: "Presets" })).toHaveFocus();
    });
    const remove = within(section).getByRole("button", { name: "Excluir o preset Fornecedores" });

    await user.click(remove);
    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(remove).toHaveFocus();
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

  it("If-Match recusado (428) mostra a falha na seção e mantém o preset", async () => {
    const { user } = setup({
      [DELETE_ROUTE]: () => problemResponse(428, "PRECONDITION_REQUIRED"),
    });

    const section = await presetsSection();
    await user.click(
      await within(section).findByRole("button", { name: "Excluir o preset Clientes do mês" }),
    );
    await user.click(screen.getByRole("button", { name: "Excluir preset" }));

    expect(await within(section).findByRole("alert")).toHaveTextContent(
      "O pedido foi recusado. Recarregue a página e tente de novo.",
    );
    expect(within(section).getByRole("heading", { name: "Clientes do mês" })).toBeVisible();
  });

  // O diálogo só fecha com a lista relida, que decide o destino do foco; até lá, o botão diz que
  // a exclusão segue em curso, para o diálogo não parecer travado.
  it("excluído (204) com a lista ainda sendo relida: o diálogo diz que está excluindo", async () => {
    const relist = heldResponse();
    let deleted = false;
    const { user } = setup({
      "GET /api/v1/presets": () =>
        deleted ? relist.handler() : jsonResponse({ data: [presetBody()] }),
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
    await user.click(within(dialog).getByRole("button", { name: "Excluir preset" }));

    expect(
      await within(dialog).findByRole("button", { name: "Excluindo preset…" }),
    ).toHaveAttribute("aria-disabled", "true");
    relist.answer(jsonResponse({ data: [] }));

    await waitFor(() => {
      expect(within(section).getByRole("heading", { level: 2, name: "Presets" })).toHaveFocus();
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("com a exclusão em voo, o Esc não fecha; fechado pelo navegador, reabre e a falha aparece", async () => {
    const held = heldResponse();
    const { api, user } = setup({ [DELETE_ROUTE]: held.handler });
    const section = await presetsSection();
    const remove = await within(section).findByRole("button", {
      name: "Excluir o preset Clientes do mês",
    });
    await user.click(remove);
    const dialog = screen.getByRole("dialog", { name: "Excluir o preset Clientes do mês?" });
    await user.click(within(dialog).getByRole("button", { name: "Excluir preset" }));
    await waitFor(() => {
      expect(sent(api.requests, DELETE_ROUTE)).toHaveLength(1);
    });

    expect(escapeIsBlocked(dialog)).toBe(true);
    forceClose(dialog);
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(remove);
    expect(screen.getByRole("dialog", { name: "Excluir o preset Clientes do mês?" })).toBeVisible();
    held.answer(problemResponse(503, "SERVICE_UNAVAILABLE"));

    expect(await within(section).findByRole("alert")).toHaveTextContent(
      "O serviço está indisponível no momento. Tente de novo mais tarde.",
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sent(api.requests, DELETE_ROUTE)).toHaveLength(1);
  });
});
