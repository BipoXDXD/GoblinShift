import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { axeViolations } from "../../test/axe";
import { Field } from "./Field";

describe("Field", () => {
  it("dá ao controle o nome do label", () => {
    render(
      <Field id="nome" label="Nome">
        {(control) => <input {...control} />}
      </Field>,
    );

    expect(screen.getByRole("textbox", { name: "Nome" })).toHaveAttribute("id", "nome");
  });

  it("liga a descrição ao controle", () => {
    render(
      <Field id="nome" label="Nome" description="Como aparece no relatório.">
        {(control) => <input {...control} />}
      </Field>,
    );

    expect(screen.getByRole("textbox")).toHaveAccessibleDescription("Como aparece no relatório.");
  });

  it("sem erro, o controle não fica inválido nem ganha descrição", () => {
    render(
      <Field id="nome" label="Nome">
        {(control) => <input {...control} />}
      </Field>,
    );

    const input = screen.getByRole("textbox");
    expect(input).not.toHaveAttribute("aria-invalid");
    expect(input).not.toHaveAttribute("aria-describedby");
  });

  it("com erro, marca o controle como inválido e anuncia o erro em texto", () => {
    render(
      <Field id="nome" label="Nome" error="Informe o nome.">
        {(control) => <input {...control} />}
      </Field>,
    );

    const input = screen.getByRole("textbox");
    expect(input).toBeInvalid();
    expect(input).toHaveAccessibleDescription("Erro: Informe o nome.");
  });

  it("lê a descrição e depois o erro", () => {
    render(
      <Field id="nome" label="Nome" description="Até 10 letras." error="Nome longo demais.">
        {(control) => <input {...control} />}
      </Field>,
    );

    expect(screen.getByRole("textbox")).toHaveAccessibleDescription(
      "Até 10 letras. Erro: Nome longo demais.",
    );
  });

  it("funciona com select", () => {
    render(
      <Field id="modo" label="Modo">
        {(control) => (
          <select {...control}>
            <option value="a">A</option>
          </select>
        )}
      </Field>,
    );

    expect(screen.getByRole("combobox", { name: "Modo" })).toBeInTheDocument();
  });

  it("não tem violações do axe", async () => {
    const { container } = render(
      <Field id="nome" label="Nome" description="Ajuda." error="Erro.">
        {(control) => <input {...control} />}
      </Field>,
    );

    expect(await axeViolations(container)).toEqual([]);
  });
});
