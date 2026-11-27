import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("tem título e conteúdo", () => {
    render(
      <EmptyState title="Nenhum resultado">
        <p>Os arquivos expiraram.</p>
      </EmptyState>,
    );

    expect(screen.getByRole("heading", { level: 2, name: "Nenhum resultado" })).toBeInTheDocument();
    expect(screen.getByText("Os arquivos expiraram.")).toBeInTheDocument();
  });

  it("aceita outro nível de título", () => {
    render(<EmptyState title="Vazio" headingLevel={3} />);

    expect(screen.getByRole("heading", { level: 3, name: "Vazio" })).toBeInTheDocument();
  });

  it("renderiza a ilustração recebida no espaço reservado", () => {
    render(<EmptyState title="Vazio" illustration={<svg data-testid="goblin" aria-hidden />} />);

    expect(screen.getByTestId("goblin")).toBeInTheDocument();
  });

  // A goblin colada no título (8 px, o gap do texto) parecia parte dele: 24 px a separam do grupo.
  it("dá respiro de mb-4 (16 px + gap) entre a ilustração e o título", () => {
    render(<EmptyState title="Vazio" illustration={<svg data-testid="goblin" aria-hidden />} />);

    expect(screen.getByTestId("goblin").parentElement).toHaveClass("mb-4");
  });

  it("sem ilustração não deixa espaço vazio acima do título", () => {
    render(<EmptyState title="Vazio" />);

    expect(screen.getByRole("heading", { name: "Vazio" }).previousElementSibling).toBeNull();
  });
});
