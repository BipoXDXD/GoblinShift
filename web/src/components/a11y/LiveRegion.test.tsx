import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LiveRegion } from "./LiveRegion";

describe("LiveRegion", () => {
  it("existe vazia antes da primeira mensagem, para o leitor de tela já a conhecer", () => {
    render(<LiveRegion message={null} />);

    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("anuncia com educação por padrão", () => {
    render(<LiveRegion message="Envio concluído." />);

    const region = screen.getByRole("status");
    expect(region).toHaveTextContent("Envio concluído.");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("aria-atomic", "true");
  });

  it("interrompe quando pedido", () => {
    render(<LiveRegion message="Falhou." politeness="assertive" />);

    expect(screen.getByRole("alert")).toHaveTextContent("Falhou.");
  });

  it("troca o texto sem recriar a região", () => {
    const { rerender } = render(<LiveRegion message="Um." />);
    const region = screen.getByRole("status");

    rerender(<LiveRegion message="Dois." />);

    expect(screen.getByRole("status")).toBe(region);
    expect(region).toHaveTextContent("Dois.");
  });
});
