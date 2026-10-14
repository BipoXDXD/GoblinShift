import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { axeViolations } from "../../test/axe";
import { ErrorSummary } from "./ErrorSummary";

const issues = [
  { fieldId: "arquivos", message: "Escolha pelo menos um arquivo." },
  { fieldId: null, message: "A versão da receita foi recusada." },
];

function Form({ attempt }: { readonly attempt: number }) {
  return (
    <>
      <ErrorSummary issues={issues} attempt={attempt} />
      <label htmlFor="arquivos">Arquivos</label>
      <input id="arquivos" />
    </>
  );
}

describe("ErrorSummary", () => {
  it("não renderiza nada sem erros", () => {
    const { container } = render(<ErrorSummary issues={[]} attempt={1} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("recebe o foco e lista os erros", () => {
    render(<Form attempt={1} />);

    const summary = screen.getByRole("alert");
    expect(summary).toHaveFocus();
    expect(summary).toHaveTextContent("Escolha pelo menos um arquivo.");
    expect(summary).toHaveTextContent("A versão da receita foi recusada.");
  });

  it("leva o foco ao campo pelo link do erro", async () => {
    const user = userEvent.setup();
    render(<Form attempt={1} />);

    await user.click(screen.getByRole("link", { name: "Escolha pelo menos um arquivo." }));

    expect(screen.getByRole("textbox", { name: "Arquivos" })).toHaveFocus();
  });

  it("erro sem campo fica como texto, sem link", () => {
    render(<Form attempt={1} />);

    expect(screen.queryByRole("link", { name: "A versão da receita foi recusada." })).toBeNull();
  });

  it("volta a receber o foco a cada nova tentativa", () => {
    const { rerender } = render(<Form attempt={1} />);
    screen.getByRole("textbox").focus();

    rerender(<Form attempt={2} />);

    expect(screen.getByRole("alert")).toHaveFocus();
  });

  it("não toma o foco quando os erros somem e voltam sem nova tentativa", () => {
    const { rerender } = render(<ErrorSummary issues={issues} attempt={1} />);
    rerender(<ErrorSummary issues={[]} attempt={1} />);
    const field = document.createElement("input");
    document.body.append(field);
    field.focus();

    rerender(<ErrorSummary issues={issues} attempt={1} />);

    expect(field).toHaveFocus();
    field.remove();
  });

  it("toma o foco quando o erro do servidor chega depois da tentativa", () => {
    const { rerender } = render(<ErrorSummary issues={[]} attempt={1} />);

    rerender(<ErrorSummary issues={issues} attempt={1} />);

    expect(screen.getByRole("alert")).toHaveFocus();
  });

  it("não tem violações do axe", async () => {
    const { container } = render(<Form attempt={1} />);

    expect(await axeViolations(container)).toEqual([]);
  });
});
