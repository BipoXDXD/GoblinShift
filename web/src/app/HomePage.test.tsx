import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { appRoutes, preloadRoutes } from "../test/appRoutes";
import { axeViolations } from "../test/axe";
import { fakeApi, jsonResponse, problemResponse, runBody, screenRequests } from "../test/fakeApi";
import { renderRoutes } from "../test/renderRoutes";

await preloadRoutes("/", "/historico");

type Handler = () => Response | null;

const EMPTY = { data: [], next_cursor: null };

function setup(runs: Handler, path = "/") {
  const api = fakeApi({ "GET /api/v1/runs": runs });
  return { ...renderRoutes({ routes: appRoutes(api.client), path, client: api.client }), api };
}

function recentSection() {
  return screen.findByRole("region", { name: "Execuções recentes" });
}

describe("Oficina: execuções recentes", () => {
  it("mostra as cinco mais novas com uma chamada só e leva ao histórico", async () => {
    const runs = Array.from({ length: 5 }, (_, index) =>
      runBody({ id: `0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3${String(index)}` }),
    );
    const { api } = setup(() => jsonResponse({ data: runs, next_cursor: "mais" }));

    const section = await recentSection();
    expect(await within(section).findAllByRole("listitem")).toHaveLength(5);
    const runsRequests = screenRequests(api.requests).filter(
      (request) => new URL(request.url).pathname === "/api/v1/runs",
    );
    expect(runsRequests.map((request) => new URL(request.url).search)).toEqual(["?limit=5"]);
    expect(within(section).getByRole("link", { name: "Ver o histórico completo" })).toHaveAttribute(
      "href",
      "/historico",
    );
  });

  it("sem execuções, convida a escolher uma receita", async () => {
    setup(() => jsonResponse({ data: [], next_cursor: null }));

    const section = await recentSection();
    expect(
      await within(section).findByRole("heading", { name: "Nenhuma execução ainda" }),
    ).toBeVisible();
    expect(section).toHaveTextContent("Escolha uma receita acima para começar.");
    // Uma goblin por tela: na Oficina é a Rúbia da abertura; o vazio da lista fica só em texto.
    expect(screen.getAllByRole("img").map((img) => img.getAttribute("alt"))).toEqual([
      expect.stringMatching(/^Rúbia, /),
    ]);
  });

  it("sem login, o aviso fica dentro da seção, com título de nível 3", async () => {
    setup(() => problemResponse(401, "AUTHENTICATION_REQUIRED"));

    const section = await recentSection();
    expect(
      await within(section).findByRole("heading", { level: 3, name: "Entre para continuar" }),
    ).toBeVisible();
  });

  it("sem o cookie de sessão, convida a entrar sem chamar o /me nem a lista", async () => {
    const api = fakeApi({ "GET /api/v1/runs": () => jsonResponse(EMPTY) }, { csrfToken: null });
    renderRoutes({ routes: appRoutes(api.client), path: "/", client: api.client });

    const section = await recentSection();
    expect(
      await within(section).findByRole("heading", { level: 3, name: "Entre para continuar" }),
    ).toBeVisible();
    expect(api.requests).toEqual([]);
  });

  it("com o /me dizendo que a sessão acabou, não chama a lista", async () => {
    const api = fakeApi({
      "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED"),
      "GET /api/v1/runs": () => jsonResponse(EMPTY),
    });
    renderRoutes({ routes: appRoutes(api.client), path: "/", client: api.client });

    const section = await recentSection();
    expect(
      await within(section).findByRole("heading", { level: 3, name: "Entre para continuar" }),
    ).toBeVisible();
    expect(screenRequests(api.requests)).toEqual([]);
  });

  it("enquanto o /me não confirma a sessão, não chama a lista", async () => {
    const api = fakeApi({
      "GET /api/v1/me": () => null,
      "GET /api/v1/runs": () => jsonResponse(EMPTY),
    });
    renderRoutes({ routes: appRoutes(api.client), path: "/", client: api.client });

    expect(
      await within(await recentSection()).findByText("Carregando as execuções."),
    ).toBeVisible();
    expect(screenRequests(api.requests)).toEqual([]);
  });

  it("falha da lista não derruba a Oficina", async () => {
    setup(() => problemResponse(503, "SERVICE_UNAVAILABLE"));

    const section = await recentSection();
    expect(await within(section).findByRole("alert")).toHaveTextContent(/indisponível/);
    expect(screen.getAllByRole("link", { name: /Abrir a Bancada/ })).toHaveLength(5);
  });

  it("não tem violações do axe", async () => {
    const { container } = setup(() => jsonResponse({ data: [runBody()], next_cursor: null }));
    await within(await recentSection()).findAllByRole("listitem");

    expect(await axeViolations(container)).toEqual([]);
  });
});

describe("Oficina: primeiro uso", () => {
  it("a abertura tem uma ação primária que leva à Bancada do Image Forge com exemplo", async () => {
    setup(() => jsonResponse(EMPTY));

    const trySample = await screen.findByRole("link", { name: "Testar com um exemplo" });
    expect(trySample).toHaveAttribute("href", "/bancada/image-forge?exemplo=1");
    // Antes da cena 3D na ordem do DOM: no celular, a cena e os cartões ficam abaixo da dobra.
    const scene = screen.getByRole("img", { name: /^Rúbia, / });
    expect(trySample.compareDocumentPosition(scene) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    const orChoose = screen.getByRole("link", { name: "ou escolha uma receita" });
    expect(orChoose).toHaveAttribute("href", "#receitas");
    expect(document.getElementById("receitas")).toHaveAccessibleName("Receitas");
  });

  it("Oficina sem execuções convida para o exemplo", async () => {
    setup(() => jsonResponse(EMPTY));

    const section = await recentSection();
    await within(section).findByRole("heading", { name: "Nenhuma execução ainda" });
    expect(section).toHaveTextContent("Primeira vez? Teste com um arquivo de exemplo.");
    expect(
      within(section).getByRole("link", { name: "Teste com um arquivo de exemplo." }),
    ).toHaveAttribute("href", "/bancada/image-forge?exemplo=1");
  });

  it("com execuções no histórico, não convida para o exemplo", async () => {
    setup(() => jsonResponse({ data: [runBody()], next_cursor: null }));

    const section = await recentSection();
    await within(section).findAllByRole("listitem");
    expect(section).not.toHaveTextContent("Primeira vez?");
  });
});

describe("navegação", () => {
  it("o cabeçalho leva ao Histórico e marca a página atual", async () => {
    setup(() => jsonResponse({ data: [], next_cursor: null }), "/historico");

    const link = await screen.findByRole("link", { name: "Histórico" });
    expect(link).toHaveAttribute("href", "/historico");
    expect(link).toHaveAttribute("aria-current", "page");
  });
});
