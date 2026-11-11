import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Heading } from "./Heading";

describe("Heading", () => {
  it.each([2, 3, 4] as const)("renderiza o título no nível %i", (level) => {
    render(<Heading level={level}>Título</Heading>);

    expect(screen.getByRole("heading", { level, name: "Título" })).toBeInTheDocument();
  });

  it("repassa os atributos para a tag", () => {
    render(
      <Heading level={3} id="titulo" className="font-semibold">
        Título
      </Heading>,
    );

    const heading = screen.getByRole("heading", { level: 3, name: "Título" });
    expect(heading).toHaveAttribute("id", "titulo");
    expect(heading).toHaveClass("font-semibold");
  });
});
