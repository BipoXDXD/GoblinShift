import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { formatDateTime } from "../format";
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

const REPORT_ID = "7d3c2a1e-0000-4000-8000-0000000000bb";
const EXPIRES_AT = "2026-10-16T12:00:00Z";

type Availability = "available" | "expired" | "deleted" | "missing";

function packageRef(availability: Availability) {
  return {
    artifact_id: PACKAGE_ID,
    logical_name: "pacote",
    role: "package",
    file_name: "csv-janitor.zip",
    availability,
    source_artifact_id: null,
  };
}

function reportRef(availability: Availability) {
  return {
    artifact_id: REPORT_ID,
    logical_name: "relatorio",
    role: "report",
    file_name: "relatorio.json",
    availability,
    source_artifact_id: null,
  };
}

function resultsWith(...artifacts: readonly object[]) {
  return { published: true, expires_at: EXPIRES_AT, artifacts };
}

const results = resultsWith(packageRef("available"));

function resultsHandler(body: object): Handler {
  return () => jsonResponse(body);
}

function setup(handlers: Readonly<Record<string, Handler>> = {}) {
  const api = fakeApi(
    {
      [`GET /api/v1/runs/${RUN_ID}`]: () => jsonResponse(runBody({ status: "succeeded" })),
      [`GET /api/v1/runs/${RUN_ID}/results`]: () => jsonResponse(results),
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

describe("Resultados: disponibilidade vem do /results", () => {
  it("disponíveis: os downloads e o Excluir resultados, sem consultar cada artefato", async () => {
    const { api } = setup();

    const region = await resultsRegion();
    expect(await within(region).findByRole("button", { name: OPEN_BUTTON })).toBeVisible();
    expect(within(region).getByRole("link", { name: /^Baixar csv-janitor\.zip/ })).toBeVisible();
    expect(
      screenRequests(api.requests).filter((request) =>
        new URL(request.url).pathname.startsWith("/api/v1/artifacts/"),
      ),
    ).toEqual([]);
  });

  it("todos excluídos: sem links nem Excluir, com o estado vazio que diz por quê", async () => {
    setup({
      [`GET /api/v1/runs/${RUN_ID}/results`]: resultsHandler(
        resultsWith(packageRef("deleted"), reportRef("deleted")),
      ),
    });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("heading", { name: "Resultados excluídos" }),
    ).toBeVisible();
    expect(within(region).queryByRole("link", { name: /^Baixar/ })).toBeNull();
    expect(within(region).queryByRole("button", { name: OPEN_BUTTON })).toBeNull();
  });

  it("todos expirados: sem links, com a data em que venceram (sem prazo fixo no texto)", async () => {
    setup({
      [`GET /api/v1/runs/${RUN_ID}/results`]: resultsHandler(
        resultsWith(packageRef("expired"), reportRef("expired")),
      ),
    });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("heading", { name: "Resultados expirados" }),
    ).toBeVisible();
    expect(region).toHaveTextContent(`Os resultados expiraram em ${formatDateTime(EXPIRES_AT)}.`);
    expect(region).not.toHaveTextContent("48 horas");
    expect(within(region).queryByRole("link", { name: /^Baixar/ })).toBeNull();
    expect(within(region).queryByRole("button", { name: OPEN_BUTTON })).toBeNull();
  });

  it("todos perdidos: sem links nem Excluir, explicando que os arquivos se perderam", async () => {
    setup({
      [`GET /api/v1/runs/${RUN_ID}/results`]: resultsHandler(
        resultsWith(packageRef("missing"), reportRef("missing")),
      ),
    });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("heading", { name: "Resultados perdidos" }),
    ).toBeVisible();
    expect(region).toHaveTextContent(
      "Os arquivos gerados por esta execução se perderam e não podem mais ser baixados. Os que você enviou não mudam.",
    );
    expect(within(region).queryByRole("link", { name: /^Baixar/ })).toBeNull();
    expect(within(region).queryByRole("button", { name: OPEN_BUTTON })).toBeNull();
  });

  it("um arquivo perdido entre disponíveis: sem link para ele, dizendo que se perdeu", async () => {
    setup({
      [`GET /api/v1/runs/${RUN_ID}/results`]: resultsHandler(
        resultsWith(packageRef("available"), reportRef("missing")),
      ),
    });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("link", { name: /^Baixar csv-janitor\.zip/ }),
    ).toBeVisible();
    expect(within(region).queryByRole("link", { name: /relatorio\.json/ })).toBeNull();
    expect(within(region).getByRole("listitem")).toHaveTextContent(
      "relatorio.json (relatório): perdido.",
    );
  });

  it("nenhum disponível por motivos diferentes: estado vazio de indisponíveis", async () => {
    setup({
      [`GET /api/v1/runs/${RUN_ID}/results`]: resultsHandler(
        resultsWith(packageRef("expired"), reportRef("deleted")),
      ),
    });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("heading", { name: "Resultados indisponíveis" }),
    ).toBeVisible();
    expect(within(region).queryByRole("link", { name: /^Baixar/ })).toBeNull();
    expect(within(region).queryByRole("button", { name: OPEN_BUTTON })).toBeNull();
  });

  it("um arquivo expirado entre disponíveis: sem link para ele, com a data em que expirou", async () => {
    setup({
      [`GET /api/v1/runs/${RUN_ID}/results`]: resultsHandler(
        resultsWith(packageRef("available"), reportRef("expired")),
      ),
    });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("link", { name: /^Baixar csv-janitor\.zip/ }),
    ).toBeVisible();
    expect(within(region).queryByRole("link", { name: /relatorio\.json/ })).toBeNull();
    expect(within(region).getByRole("listitem")).toHaveTextContent(
      `relatorio.json (relatório): expirou em ${formatDateTime(EXPIRES_AT)}.`,
    );
    // O prazo já passou: "Disponíveis até" a mesma data contradiria o motivo.
    expect(region).not.toHaveTextContent("Disponíveis até");
    expect(within(region).getByRole("button", { name: OPEN_BUTTON })).toBeVisible();
  });

  it("o pacote excluído entre disponíveis: sem o Baixar primário, com o motivo", async () => {
    setup({
      [`GET /api/v1/runs/${RUN_ID}/results`]: resultsHandler(
        resultsWith(packageRef("deleted"), reportRef("available")),
      ),
    });

    const region = await resultsRegion();
    expect(
      await within(region).findByRole("link", { name: /^Baixar relatorio\.json/ }),
    ).toBeVisible();
    expect(within(region).queryByRole("link", { name: /csv-janitor\.zip/ })).toBeNull();
    expect(region).toHaveTextContent("csv-janitor.zip (pacote com todos os arquivos): excluído.");
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
      [`GET /api/v1/runs/${RUN_ID}/results`]: () =>
        jsonResponse(resultsWith(packageRef(deleted ? "deleted" : "available"))),
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

  it("com o pedido em voo, Excluir fica bloqueado com o progresso e não sai outro DELETE", async () => {
    // null: o DELETE fica em voo até o fim do teste.
    const { api, user } = setup({ [DELETE_ROUTE]: () => null });

    const region = await resultsRegion();
    await user.click(await within(region).findByRole("button", { name: OPEN_BUTTON }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Excluir resultados" }),
    );
    const busy = await within(region).findByRole("button", { name: "Excluindo resultados…" });
    expect(busy).toHaveAttribute("aria-disabled", "true");
    await user.click(busy);

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sent(api.requests, DELETE_ROUTE)).toHaveLength(1);
  });

  it("o 202 basta: some com os links e anuncia, mesmo que a releitura ainda diga disponível", async () => {
    const { user } = setup({ [DELETE_ROUTE]: () => new Response(null, { status: 202 }) });

    const region = await resultsRegion();
    await user.click(await within(region).findByRole("button", { name: OPEN_BUTTON }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Excluir resultados" }),
    );

    expect(
      await within(region).findByRole("heading", { name: "Resultados excluídos" }),
    ).toBeVisible();
    expect(within(region).queryByRole("link", { name: /^Baixar/ })).toBeNull();
    expect(screen.getByText("Resultados excluídos.")).toBeInTheDocument();
  });

  it("execução que já não existe (404): avisa e relê a execução", async () => {
    let runReads = 0;
    const { user } = setup({
      [`GET /api/v1/runs/${RUN_ID}`]: () => {
        runReads += 1;
        return jsonResponse(runBody({ status: "succeeded" }));
      },
      [DELETE_ROUTE]: () => problemResponse(404, "NOT_FOUND"),
    });

    const region = await resultsRegion();
    await user.click(await within(region).findByRole("button", { name: OPEN_BUTTON }));
    const readsBefore = runReads;
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Excluir resultados" }),
    );

    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "Esta execução não existe mais",
    );
    await waitFor(() => {
      expect(runReads).toBeGreaterThan(readsBefore);
    });
  });

  it("execução que não terminou (409): diz que só dá para excluir depois e os downloads continuam", async () => {
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
