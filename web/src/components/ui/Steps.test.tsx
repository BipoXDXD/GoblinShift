import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Steps } from "./Steps";

describe("Steps", () => {
  it("marca a fase atual com aria-current e diz em texto as concluídas", () => {
    render(<Steps phase="packaging" />);
    const items = screen.getAllByRole("listitem");

    expect(items.map((item) => item.textContent)).toEqual([
      "Inspecionando as entradas (concluída)",
      "Processando os arquivos (concluída)",
      "Empacotando os resultados",
      "Finalizando",
    ]);
    expect(screen.getByText("Empacotando os resultados").closest("li")).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(items.filter((item) => item.hasAttribute("aria-current"))).toHaveLength(1);
  });

  it("na primeira fase nada está concluído", () => {
    render(<Steps phase="inspecting" />);

    expect(screen.queryByText(/concluída/)).toBeNull();
  });

  it("aguardando nova tentativa não marca nenhuma fase como atual", () => {
    render(<Steps phase="retry_wait" />);

    expect(screen.getAllByRole("listitem").some((item) => item.hasAttribute("aria-current"))).toBe(
      false,
    );
  });

  it("a lista tem nome acessível", () => {
    render(<Steps phase="processing" />);

    expect(screen.getByRole("list", { name: "Etapas da execução" })).toBeInTheDocument();
  });
});
