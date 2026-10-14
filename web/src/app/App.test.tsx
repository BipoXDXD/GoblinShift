import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renderiza a rota inicial", () => {
    window.history.pushState({}, "", "/");

    render(<App />);

    expect(screen.getByRole("heading", { level: 1, name: "GoblinShift" })).toBeInTheDocument();
  });

  it("a rota inicial leva à Bancada de cada receita", () => {
    window.history.pushState({}, "", "/");

    render(<App />);

    expect(
      screen.getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")]),
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
