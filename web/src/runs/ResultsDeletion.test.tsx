import { screen, waitFor, within } from "@testing-library/react";
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
import { renderRoutes } from "../test/renderRoutes";

type Handler = (request: Request) => Response | Promise<Response> | null;

const CSRF = "csrf-da-sessao";
const PACKAGE_ID = "7d3c2a1e-0000-4000-8000-0000000000aa";
const DELETE_ROUTE = `DELETE /api/v1/runs/${RUN_ID}/artifacts`;
const OPEN_BUTTON = "Excluir resultados";

const results = {
  published: true,
  expires_at: "2026-10-16T12:00:00Z",
  artifacts: [
    {
      artifact_id: PACKAGE_ID,
      logical_name: "pacote",
      role: "package",
      file_name: "csv-janitor.zip",
    },
  ],
};

function artifactWith(availability: string): Handler {
  return () =>
    jsonResponse({
      id: PACKAGE_ID,
      role: "package",
      size: 22,
      sha256: "0".repeat(64),
      expires_at: "2026-10-16T12:00:00Z",
      availability,
    });
}

function setup(handlers: Readonly<Record<string, Handler>> = {}) {
  const api = fakeApi(
    {
      [`GET /api/v1/runs/${RUN_ID}`]: () => jsonResponse(runBody({ status: "succeeded" })),
      [`GET /api/v1/runs/${RUN_ID}/results`]: () => jsonResponse(results),
      [`GET /api/v1/artifacts/${PACKAGE_ID}`]: artifactWith("available"),
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

function sent(requests: readonly Request[], route: string): Request[] {
  return screenRequests(requests).filter(
    (request) => `${request.method} ${new URL(request.url).pathname}` === route,
  );
}

async function resultsRegion(): Promise<HTMLElement> {
  return screen.findByRole("region", { name: "Resultados" });
}

describe("Resultados: disponibilidade", () => {
  it("disponíveis: os downloads e o Excluir resultados", async () => {
    setup();

    const region = await resultsRegion();
    expect(await within(region).findByRole("button", { name: OPEN_BUTTON })).toBeVisible();
    expect(within(region).getByRole("link", { name: /^Baixar csv-janitor\.zip/ })).toBeVisible();
  });

  it("excluídos: sem links, com o estado vazio que diz por quê", async () => {
    setup({ [`GET /api/v1/artifacts/${PACKAGE_ID}`]: artifactWith("deleted") });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("heading", { name: "Resultados excluídos" }),
    ).toBeVisible();
    expect(within(region).queryByRole("link", { name: /^Baixar/ })).toBeNull();
    expect(within(region).queryByRole("button", { name: OPEN_BUTTON })).toBeNull();
  });

  it("expirados: sem links, com o prazo de 48 horas", async () => {
    setup({ [`GET /api/v1/artifacts/${PACKAGE_ID}`]: artifactWith("expired") });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("heading", { name: "Resultados expirados" }),
    ).toBeVisible();
    expect(region).toHaveTextContent("48 horas");
    expect(within(region).queryByRole("link", { name: /^Baixar/ })).toBeNull();
  });

  it("se a disponibilidade não vier, os links continuam (o download diz o resto)", async () => {
    setup({
      [`GET /api/v1/artifacts/${PACKAGE_ID}`]: () => problemResponse(500, "INTERNAL_ERROR"),
    });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("link", { name: /^Baixar csv-janitor\.zip/ }),
    ).toBeVisible();
  });
});

describe("Resultados: excluir", () => {
  it("pede confirmação com o foco em cancelar; cancelar não chama a API", async () => {
    const { api, user } = setup();

    const region = await resultsRegion();
    await user.click(await within(region).findByRole("button", { name: OPEN_BUTTON }));
    const dialog = screen.getByRole("dialog", { name: "Excluir os resultados desta execução?" });
    expect(within(dialog).getByRole("button", { name: "Cancelar" })).toHaveFocus();
    expect(await axeViolations(document.body)).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));

    expect(sent(api.requests, DELETE_ROUTE)).toEqual([]);
  });

  it("confirmado: DELETE com CSRF, anúncio, estado excluído e foco no título", async () => {
    let deleted = false;
    const { api, user } = setup({
      [DELETE_ROUTE]: () => {
        deleted = true;
        return new Response(null, { status: 202 });
      },
      [`GET /api/v1/artifacts/${PACKAGE_ID}`]: (request) =>
        artifactWith(deleted ? "deleted" : "available")(request),
    });

    const region = await resultsRegion();
    await user.click(await within(region).findByRole("button", { name: OPEN_BUTTON }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Excluir resultados" }),
    );

    expect(await screen.findByText("Resultados excluídos.")).toBeInTheDocument();
    expect(sent(api.requests, DELETE_ROUTE)[0]?.headers.get("X-CSRF-Token")).toBe(CSRF);
    expect(
      await within(region).findByRole("heading", { name: "Resultados excluídos" }),
    ).toBeVisible();
    await waitFor(() => {
      expect(within(region).getByRole("heading", { name: "Resultados", level: 2 })).toHaveFocus();
    });
  });

  it("falha do pedido aparece e os downloads continuam", async () => {
    const { user } = setup({ [DELETE_ROUTE]: () => problemResponse(409, "STATE_CONFLICT") });

    const region = await resultsRegion();
    await user.click(await within(region).findByRole("button", { name: OPEN_BUTTON }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Excluir resultados" }),
    );

    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "A execução ainda não terminou; os resultados só podem ser excluídos depois.",
    );
    expect(within(region).getByRole("link", { name: /^Baixar csv-janitor\.zip/ })).toBeVisible();
  });
});
