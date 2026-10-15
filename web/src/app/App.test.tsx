import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { problemResponse } from "../test/fakeApi";

import { App } from "./App";

describe("App", () => {
  it("renderiza a rota inicial", async () => {
    window.history.pushState({}, "", "/");

    render(<App />);

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: "Arquivos em lote, limpos pelos goblins da oficina.",
      }),
    ).toBeInTheDocument();
  });

  it("toda tela tem a marca levando à Oficina e o atalho para o conteúdo", () => {
    window.history.pushState({}, "", "/rota-que-nao-existe");

    render(<App />);

    expect(screen.getByRole("link", { name: "GoblinShift, Oficina" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Pular para o conteúdo" })).toHaveAttribute(
      "href",
      `#${screen.getByRole("main").id}`,
    );
  });

  it("a rota inicial leva à Bancada de cada receita", async () => {
    window.history.pushState({}, "", "/");

    render(<App />);
    await screen.findByRole("heading", { level: 1 });

    expect(
      within(screen.getByRole("main"))
        .getAllByRole("link")
        .map((link) => [link.textContent, link.getAttribute("href")]),
    ).toEqual([
      ["Abrir a Bancada da CSV Janitor", "/bancada/csv-janitor"],
      ["Abrir a Bancada da Image Forge", "/bancada/image-forge"],
      ["Abrir a Bancada da Downloads Detox", "/bancada/downloads-detox"],
    ]);
  });

  it("o nome acessível do link de cada cartão separa as palavras", async () => {
    window.history.pushState({}, "", "/");

    render(<App />);

    expect(
      await screen.findByRole("link", { name: "Abrir a Bancada da CSV Janitor" }),
    ).toHaveAttribute("href", "/bancada/csv-janitor");
  });

  it("sem login, a Bancada pede para entrar sem repetir o pedido do catálogo", async () => {
    const fetchMock = vi.fn<typeof fetch>(() =>
      Promise.resolve(problemResponse(401, "AUTHENTICATION_REQUIRED")),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.history.pushState({}, "", "/bancada/csv-janitor");

    render(<App />);

    const prompt = await screen.findByRole("region", { name: "Entre para continuar" });
    expect(within(prompt).getByRole("link", { name: "Entrar com Google" })).toHaveAttribute(
      "href",
      "/auth/login/google?return_to=%2Fbancada%2Fcsv-janitor",
    );
    const catalogCalls = fetchMock.mock.calls.filter(([input]) =>
      new Request(input).url.endsWith("/api/v1/catalog"),
    );
    expect(catalogCalls).toHaveLength(1);
  });

  it("mostra página não encontrada para rota desconhecida", () => {
    window.history.pushState({}, "", "/rota-que-nao-existe");

    render(<App />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Página não encontrada" }),
    ).toBeInTheDocument();
  });
});
