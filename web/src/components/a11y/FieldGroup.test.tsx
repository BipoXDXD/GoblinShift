import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { axeViolations } from "../../test/axe";
import { FieldGroup } from "./FieldGroup";

describe("FieldGroup", () => {
  it("dá ao grupo o nome da legenda", () => {
    render(
      <FieldGroup id="modo" legend="Modo">
        <input type="radio" name="m" aria-label="A" />
      </FieldGroup>,
    );

    expect(screen.getByRole("group", { name: "Modo" })).toBeInTheDocument();
  });

  it("liga descrição e erro ao grupo", () => {
    render(
      <FieldGroup id="modo" legend="Modo" description="Escolha um." error="Obrigatório.">
        <input type="radio" name="m" aria-label="A" />
      </FieldGroup>,
    );

    expect(screen.getByRole("group")).toHaveAccessibleDescription("Escolha um. Erro: Obrigatório.");
  });

  it("não tem violações do axe", async () => {
    const { container } = render(
      <FieldGroup id="modo" legend="Modo" error="Obrigatório.">
        <input type="radio" name="m" aria-label="A" />
      </FieldGroup>,
    );

    expect(await axeViolations(container)).toEqual([]);
  });
});
