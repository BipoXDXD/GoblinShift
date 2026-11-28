import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { peekSharedNames, SHARE_TTL_MS } from "../share/inbox";
import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { catalogHandler } from "../test/catalog";
import { fakeApi, problemResponse, uploadIdFor, uploadIntentsHandler } from "../test/fakeApi";
import { renderRoutes } from "../test/renderRoutes";
import { fakeCacheStorage, seedShared } from "../test/shareInbox";

await preloadRoutes("/bancada/csv-janitor");

const SHARE_ID = "c0ffee00-0000-4000-8000-000000000002";
const PATH = `/bancada/csv-janitor?compartilhado=${SHARE_ID}`;

let caches: CacheStorage;

beforeEach(() => {
  caches = fakeCacheStorage();
  vi.stubGlobal("caches", caches);
});

function csv(name: string): File {
  return new File(["a,b\n1,2\n"], name, { type: "text/csv" });
}

function render(catalog: () => Response = catalogHandler()) {
  const deletions = Object.fromEntries(
    [1, 2].map((sequence) => [
      `DELETE /api/v1/uploads/${uploadIdFor(sequence)}`,
      () => new Response(null, { status: 202 }),
    ]),
  );
  const api = fakeApi({
    ...deletions,
    "GET /api/v1/catalog": catalog,
    "POST /api/v1/uploads": uploadIntentsHandler(),
  });
  return renderRoutes({ routes: appRoutes(api.client), path: PATH, client: api.client });
}

describe("Bancada aberta pelo compartilhamento", () => {
  it("entra com os arquivos compartilhados, que saem da caixa de entrada", async () => {
    await seedShared(caches, [csv("clientes.csv"), csv("pedidos.csv")], { id: SHARE_ID });

    render();

    const list = await screen.findByRole("list", { name: "Ordem de processamento" });
    expect(await within(list).findByText("clientes.csv")).toBeVisible();
    expect(within(list).getByText("pedidos.csv")).toBeVisible();
    expect(await peekSharedNames(caches, SHARE_ID, Date.now())).toBe("expired");
  });

  it("compartilhamento sobrevive ao login", async () => {
    await seedShared(caches, [csv("clientes.csv")], { id: SHARE_ID });

    render(() => problemResponse(401, "AUTHENTICATION_REQUIRED"));

    const notice = await screen.findByRole("region", { name: "Entre para continuar" });
    expect(within(notice).getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      `/auth/login/google?return_to=${encodeURIComponent(PATH)}`,
    );
    expect(await peekSharedNames(caches, SHARE_ID, Date.now())).toEqual(["clientes.csv"]);
  });

  it("compartilhamento vencido avisa e deixa escolher arquivos", async () => {
    await seedShared(caches, [csv("clientes.csv")], {
      id: SHARE_ID,
      at: Date.now() - SHARE_TTL_MS - 1_000,
    });

    render();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Os arquivos compartilhados expiraram ou já foram usados. Escolha os arquivos de novo.",
    );
    expect(screen.getByLabelText("Escolher arquivos")).toBeEnabled();
    expect(screen.getByText("A bancada está vazia")).toBeVisible();
  });
});
