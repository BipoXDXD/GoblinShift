import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { catalogHandler } from "../test/catalog";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { currentUrl, renderRoutes } from "../test/renderRoutes";
import { fakeCacheStorage, seedShared } from "../test/shareInbox";
import { SHARE_TTL_MS } from "./inbox";

await preloadRoutes("/compartilhar", "/");

const SHARE_ID = "c0ffee00-0000-4000-8000-000000000001";

let caches: CacheStorage;

beforeEach(() => {
  caches = fakeCacheStorage();
  vi.stubGlobal("caches", caches);
});

async function share(names: readonly string[], at = Date.now()): Promise<void> {
  await seedShared(
    caches,
    names.map((name) => new File(["x"], name)),
    { id: SHARE_ID, at },
  );
}

function render(path: string) {
  const api = fakeApi({
    "GET /api/v1/catalog": catalogHandler(),
    "GET /api/v1/runs": () => jsonResponse({ data: [], next_cursor: null }),
  });
  return renderRoutes({ routes: appRoutes(api.client), path, client: api.client });
}

describe("página do compartilhamento", () => {
  it("arquivos só de CSV vão direto para a Bancada do CSV Janitor", async () => {
    await share(["clientes.csv", "pedidos.csv"]);

    const { router } = render(`/compartilhar?id=${SHARE_ID}`);

    await waitFor(() => {
      expect(currentUrl(router)).toBe(`/bancada/csv-janitor?compartilhado=${SHARE_ID}`);
    });
  });

  it("PDF e PNG juntos mostram a escolha com as receitas que aceitam os dois", async () => {
    await share(["nota.pdf", "logo.png"]);

    render(`/compartilhar?id=${SHARE_ID}`);

    const choices = await screen.findByRole("list", { name: "Receitas para estes arquivos" });
    const links = within(choices).getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAccessibleName("Abrir a Bancada da Downloads Detox");
    expect(links[0]).toHaveAttribute("href", `/bancada/downloads-detox?compartilhado=${SHARE_ID}`);
    expect(screen.getByText("2 arquivos recebidos: nota.pdf, logo.png.")).toBeVisible();
  });

  it("compartilhamento vencido avisa e oferece todas as receitas", async () => {
    await share(["clientes.csv"], Date.now() - SHARE_TTL_MS - 1_000);

    render(`/compartilhar?id=${SHARE_ID}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Os arquivos compartilhados expiraram ou já foram usados. Escolha os arquivos de novo.",
    );
    const choices = screen.getByRole("list", { name: "Receitas para estes arquivos" });
    expect(within(choices).getAllByRole("link")).toHaveLength(5);
    expect(within(choices).getAllByRole("link")[0]).toHaveAttribute("href", "/bancada/csv-janitor");
  });

  it("sem a Cache API, trata como vencido", async () => {
    vi.stubGlobal("caches", undefined);

    render(`/compartilhar?id=${SHARE_ID}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(/expiraram ou já foram usados/);
  });

  it("a Oficina explica, depois do título, quando o compartilhamento não chegou", async () => {
    const { router } = render("/?compartilhamento=indisponivel");

    const notice = await screen.findByText(/Os arquivos compartilhados não chegaram/);
    const status = notice.closest('[role="status"]');
    expect(status).not.toBeNull();
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await waitFor(() => {
      expect(currentUrl(router)).toBe("/");
    });
    expect(notice).toBeVisible();
  });

  it("a Oficina sem o parâmetro não mostra o aviso", async () => {
    render("/");

    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/Os arquivos compartilhados não chegaram/)).toBeNull();
  });
});
