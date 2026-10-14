import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Card } from "./Card";

describe("Card", () => {
  it("como section, vira região nomeada pelo título", () => {
    render(
      <Card as="section" aria-labelledby="titulo">
        <h2 id="titulo">Resultados</h2>
      </Card>,
    );

    expect(screen.getByRole("region", { name: "Resultados" })).toBeInTheDocument();
  });

  it("sem as, é um div sem papel", () => {
    const { container } = render(<Card>conteúdo</Card>);

    expect(container.firstElementChild?.tagName).toBe("DIV");
  });
});
