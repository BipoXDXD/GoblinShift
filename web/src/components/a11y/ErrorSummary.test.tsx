import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { axeViolations } from "../../test/axe";
import { ErrorSummary } from "./ErrorSummary";

const issues = [
  { fieldId: "arquivos", label: "Arquivos", message: "Escolha pelo menos um arquivo." },
  { fieldId: null, message: "A versão da receita foi recusada." },
];

const SAME_RULE = "Informe um número inteiro de 1 a 8192.";
const sameRuleIssues = [
  { fieldId: "largura", label: "Largura máxima, em pixels", message: SAME_RULE },
  { fieldId: "altura", label: "Altura máxima, em pixels", message: SAME_RULE },
];
const acronymIssues = [{ fieldId: "pdf", label: "PDF da página 1", message: "PDF não escolhido." }];

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
    expect(summary).toHaveTextContent("Arquivos: escolha pelo menos um arquivo.");
    expect(summary).toHaveTextContent("A versão da receita foi recusada.");
  });

  it("leva o foco ao campo pelo link do erro", async () => {
    const user = userEvent.setup();
    render(<Form attempt={1} />);

    await user.click(
      screen.getByRole("link", { name: "Arquivos: escolha pelo menos um arquivo." }),
    );

    expect(screen.getByRole("textbox", { name: "Arquivos" })).toHaveFocus();
  });

  it("o link do erro tem a altura do alvo de toque", () => {
    render(<Form attempt={1} />);

    expect(
      screen.getByRole("link", { name: "Arquivos: escolha pelo menos um arquivo." }),
    ).toHaveClass("min-h-11");
  });

  it("o link cita o campo: o mesmo erro em dois campos ganha dois nomes", () => {
    render(<ErrorSummary issues={sameRuleIssues} attempt={1} />);

    expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Largura máxima, em pixels: informe um número inteiro de 1 a 8192.",
      "Altura máxima, em pixels: informe um número inteiro de 1 a 8192.",
    ]);
  });

  it("mensagem que começa com sigla mantém a sigla depois do nome do campo", () => {
    render(<ErrorSummary issues={acronymIssues} attempt={1} />);

    expect(screen.getByRole("link")).toHaveTextContent("PDF da página 1: PDF não escolhido.");
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

  it("o título é h2 por padrão", () => {
    render(<ErrorSummary issues={issues} attempt={1} />);

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Corrija os itens abaixo antes de continuar.",
      }),
    ).toBeInTheDocument();
  });

  it("aceita o nível h4, para formulário sob um h3", () => {
    render(<ErrorSummary issues={issues} attempt={1} headingLevel={4} />);

    expect(
      screen.getByRole("heading", {
        level: 4,
        name: "Corrija os itens abaixo antes de continuar.",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
  });

  it("não tem violações do axe", async () => {
    const { container } = render(<Form attempt={1} />);

    expect(await axeViolations(container)).toEqual([]);
  });
});
