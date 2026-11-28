import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  return {
    ...renderRoutes({ routes: appRoutes(api.client), path: PATH, client: api.client }),
    api,
  };
}

function uploadRequests(requests: readonly Request[]): Request[] {
  return requests.filter((request) => new URL(request.url).pathname.startsWith("/api/v1/uploads"));
}

describe("Bancada aberta pelo compartilhamento", () => {
  it("mostra os arquivos recebidos e não envia nada antes do clique", async () => {
    await seedShared(caches, [csv("clientes.csv"), csv("pedidos.csv")], { id: SHARE_ID });

    const { api } = render();

    const received = await screen.findByRole("region", { name: "Arquivos recebidos" });
    expect(within(received).getByText("clientes.csv")).toBeVisible();
    expect(within(received).getByText("pedidos.csv")).toBeVisible();
    expect(uploadRequests(api.requests)).toEqual([]);
    expect(screen.getByText("A bancada está vazia")).toBeVisible();
  });

  it("recarregar antes do clique mantém os arquivos na caixa de entrada", async () => {
    await seedShared(caches, [csv("clientes.csv")], { id: SHARE_ID });
    const first = render();
    await screen.findByRole("region", { name: "Arquivos recebidos" });
    first.unmount();

    render();

    const received = await screen.findByRole("region", { name: "Arquivos recebidos" });
    expect(within(received).getByText("clientes.csv")).toBeVisible();
    expect(await peekSharedNames(caches, SHARE_ID, Date.now())).toEqual(["clientes.csv"]);
  });

  it("a seção dos arquivos recebidos recebe o foco ao aparecer", async () => {
    await seedShared(caches, [csv("clientes.csv")], { id: SHARE_ID });

    render();

    const heading = await screen.findByRole("heading", { name: "Arquivos recebidos" });
    await waitFor(() => {
      expect(heading).toHaveFocus();
    });
  });

  it("descartar apaga da caixa, não envia nada e devolve o foco à escolha de arquivos", async () => {
    await seedShared(caches, [csv("clientes.csv")], { id: SHARE_ID });
    const { api } = render();

    await userEvent.click(await screen.findByRole("button", { name: "Descartar" }));

    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Arquivos recebidos" })).toBeNull();
    });
    expect(screen.getByLabelText("Escolher arquivos")).toHaveFocus();
    expect(await peekSharedNames(caches, SHARE_ID, Date.now())).toBe("expired");
    expect(uploadRequests(api.requests)).toEqual([]);
  });

  it("esconde o arquivo de exemplo enquanto o compartilhado espera a decisão", async () => {
    await seedShared(caches, [csv("clientes.csv")], { id: SHARE_ID });
    render();

    await screen.findByRole("region", { name: "Arquivos recebidos" });
    expect(screen.queryByRole("button", { name: "Testar com arquivo de exemplo" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Descartar" }));

    expect(
      await screen.findByRole("button", { name: "Testar com arquivo de exemplo" }),
    ).toBeVisible();
  });

  it("entra com os arquivos compartilhados depois do clique em enviar", async () => {
    await seedShared(caches, [csv("clientes.csv"), csv("pedidos.csv")], { id: SHARE_ID });

    render();
    await userEvent.click(
      await screen.findByRole("button", { name: "Enviar os arquivos recebidos" }),
    );

    const list = await screen.findByRole("list", { name: "Ordem de processamento" });
    expect(await within(list).findByText("clientes.csv")).toBeVisible();
    expect(within(list).getByText("pedidos.csv")).toBeVisible();
    expect(screen.queryByRole("region", { name: "Arquivos recebidos" })).toBeNull();
    expect(screen.getByLabelText("Escolher arquivos")).toHaveFocus();
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
