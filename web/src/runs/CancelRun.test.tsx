import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { appRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import {
  fakeApi,
  jsonResponse,
  problemResponse,
  runBody,
  RUN_ID,
  screenRequests,
} from "../test/fakeApi";
import { escapeIsBlocked, forceClose, heldResponse } from "../test/dialog";
import { renderRoutes } from "../test/renderRoutes";

type Handler = (request: Request) => Response | Promise<Response> | null;

const CSRF = "csrf-da-sessao";
const CANCEL_ROUTE = `POST /api/v1/runs/${RUN_ID}/cancel`;
const OPEN_BUTTON = "Cancelar execução";

function setup(handlers: Readonly<Record<string, Handler>>) {
  const api = fakeApi(
    {
      [`GET /api/v1/runs/${RUN_ID}/results`]: () =>
        jsonResponse({ published: false, expires_at: null, artifacts: [] }),
      ...handlers,
    },
    { csrfToken: CSRF },
  );
  const view = renderRoutes({
    routes: appRoutes(api.client),
    path: `/execucoes/${RUN_ID}`,
    client: api.client,
  });
  return { ...view, api, user: userEvent.setup() };
}

function runWith(status: string): Handler {
  return () => jsonResponse(runBody({ status }));
}

function sentCancels(requests: readonly Request[]): Request[] {
  return screenRequests(requests).filter(
    (request) =>
      request.method === "POST" && new URL(request.url).pathname.endsWith(`${RUN_ID}/cancel`),
  );
}

describe("Cancelar execução: quando aparece", () => {
  it.each(["queued", "running"])("aparece em %s", async (status) => {
    setup({ [`GET /api/v1/runs/${RUN_ID}`]: runWith(status) });

    expect(await screen.findByRole("button", { name: OPEN_BUTTON })).toBeVisible();
  });

  it.each(["stopping", "succeeded", "succeeded_with_warnings", "failed", "canceled"])(
    "não aparece em %s",
    async (status) => {
      setup({ [`GET /api/v1/runs/${RUN_ID}`]: runWith(status) });

      expect(await screen.findByText("Tentativa")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: OPEN_BUTTON })).toBeNull();
    },
  );
});

describe("Cancelar execução: confirmação", () => {
  it("pede confirmação com o foco em continuar, e continuar não chama a API", async () => {
    const { api, user } = setup({ [`GET /api/v1/runs/${RUN_ID}`]: runWith("running") });

    await user.click(await screen.findByRole("button", { name: OPEN_BUTTON }));
    const dialog = screen.getByRole("dialog", { name: "Cancelar esta execução?" });
    const keepGoing = within(dialog).getByRole("button", { name: "Continuar a execução" });
    expect(keepGoing).toHaveFocus();
    expect(await axeViolations(document.body)).toEqual([]);
    await user.click(keepGoing);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sentCancels(api.requests)).toEqual([]);
  });

  it("confirmado, manda o POST com o CSRF e anuncia o pedido", async () => {
    const { api, user } = setup({
      [`GET /api/v1/runs/${RUN_ID}`]: runWith("running"),
      [CANCEL_ROUTE]: () => jsonResponse({ id: RUN_ID, status: "stopping", error_code: null }, 202),
    });

    await user.click(await screen.findByRole("button", { name: OPEN_BUTTON }));
    const dialog = screen.getByRole("dialog", { name: "Cancelar esta execução?" });
    await user.click(within(dialog).getByRole("button", { name: OPEN_BUTTON }));

    const status = await screen.findByText(
      "Cancelamento pedido. A execução para assim que o trabalho em curso for interrompido.",
    );
    expect(status.closest("[aria-live]")).toHaveAttribute("aria-live", "polite");
    const [request] = sentCancels(api.requests);
    expect(request?.headers.get("X-CSRF-Token")).toBe(CSRF);
    expect(sentCancels(api.requests)).toHaveLength(1);
  });

  it("depois do pedido, o botão fica bloqueado até a execução mudar de estado", async () => {
    const { api, user } = setup({
      [`GET /api/v1/runs/${RUN_ID}`]: runWith("queued"),
      [CANCEL_ROUTE]: () => jsonResponse({ id: RUN_ID, status: "stopping", error_code: null }, 202),
    });

    await user.click(await screen.findByRole("button", { name: OPEN_BUTTON }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: OPEN_BUTTON }));
    const button = await screen.findByRole("button", { name: OPEN_BUTTON });
    await waitFor(() => {
      expect(button).toHaveAttribute("aria-disabled", "true");
    });
    await user.click(button);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sentCancels(api.requests)).toHaveLength(1);
  });

  it("execução que já terminou (200) anuncia que não havia o que cancelar", async () => {
    const { user } = setup({
      [`GET /api/v1/runs/${RUN_ID}`]: runWith("running"),
      [CANCEL_ROUTE]: () => jsonResponse({ id: RUN_ID, status: "succeeded", error_code: null }),
    });

    await user.click(await screen.findByRole("button", { name: OPEN_BUTTON }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: OPEN_BUTTON }));

    expect(
      await screen.findByText("A execução já tinha terminado; não havia o que cancelar."),
    ).toBeInTheDocument();
  });

  it("na fila, o servidor cancela na hora (200 canceled) e o anúncio diz isso", async () => {
    const { user } = setup({
      [`GET /api/v1/runs/${RUN_ID}`]: runWith("queued"),
      [CANCEL_ROUTE]: () =>
        jsonResponse({ id: RUN_ID, status: "canceled", error_code: "CANCELED" }),
    });

    await user.click(await screen.findByRole("button", { name: OPEN_BUTTON }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: OPEN_BUTTON }));

    expect(await screen.findByText("Execução cancelada.")).toBeInTheDocument();
  });

  it("falha do pedido mostra o erro e deixa tentar de novo", async () => {
    const { api, user } = setup({
      [`GET /api/v1/runs/${RUN_ID}`]: runWith("running"),
      [CANCEL_ROUTE]: () => problemResponse(500, "INTERNAL_ERROR"),
    });

    await user.click(await screen.findByRole("button", { name: OPEN_BUTTON }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: OPEN_BUTTON }));

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByRole("dialog")).toBeNull();
    const button = screen.getByRole("button", { name: OPEN_BUTTON });
    expect(button).not.toHaveAttribute("aria-disabled", "true");
    expect(sentCancels(api.requests)).toHaveLength(1);
  });

  it("com o pedido em voo, o Esc não fecha; fechado pelo navegador, a falha aparece e reabre", async () => {
    const held = heldResponse();
    const { api, user } = setup({
      [`GET /api/v1/runs/${RUN_ID}`]: runWith("running"),
      [CANCEL_ROUTE]: held.handler,
    });
    await user.click(await screen.findByRole("button", { name: OPEN_BUTTON }));
    const dialog = screen.getByRole("dialog", { name: "Cancelar esta execução?" });
    await user.click(within(dialog).getByRole("button", { name: OPEN_BUTTON }));
    await waitFor(() => {
      expect(sentCancels(api.requests)).toHaveLength(1);
    });

    expect(escapeIsBlocked(dialog)).toBe(true);
    forceClose(dialog);
    expect(screen.queryByRole("dialog")).toBeNull();
    held.answer(problemResponse(500, "INTERNAL_ERROR"));

    expect(await screen.findByRole("alert")).toBeVisible();
    await user.click(screen.getByRole("button", { name: OPEN_BUTTON }));
    expect(screen.getByRole("dialog", { name: "Cancelar esta execução?" })).toBeVisible();
  });

  it("quando a execução passa a parar, o foco sai do botão para o anúncio", async () => {
    const { user, queryClient } = setup({
      [`GET /api/v1/runs/${RUN_ID}`]: runWith("running"),
      [CANCEL_ROUTE]: () => jsonResponse({ id: RUN_ID, status: "stopping", error_code: null }, 202),
    });
    await user.click(await screen.findByRole("button", { name: OPEN_BUTTON }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: OPEN_BUTTON }));
    const message = await screen.findByText(/^Cancelamento pedido\./);
    act(() => {
      screen.getByRole("button", { name: OPEN_BUTTON }).focus();
    });

    // O próximo polling, sem esperar os 2 s: a resposta que o servidor daria.
    act(() => {
      queryClient.setQueryData(["runs", RUN_ID], {
        data: runBody({ status: "stopping" }),
        status: 200,
        retryAfterMs: null,
      });
    });

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: OPEN_BUTTON })).toBeNull();
    });
    expect(message.closest("[tabindex]")).toHaveFocus();
  });
});
