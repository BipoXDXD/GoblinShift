import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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

  it("mostra página não encontrada para rota desconhecida", () => {
    window.history.pushState({}, "", "/rota-que-nao-existe");

    render(<App />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Página não encontrada" }),
    ).toBeInTheDocument();
  });
});
