import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FocusHeading } from "./FocusHeading";

describe("FocusHeading", () => {
  it("recebe o foco ao montar, para a troca de etapa começar a leitura pelo título", () => {
    render(<FocusHeading>Execução</FocusHeading>);

    expect(screen.getByRole("heading", { level: 1, name: "Execução" })).toHaveFocus();
  });

  it("fica fora da ordem de Tab", () => {
    render(<FocusHeading>Execução</FocusHeading>);

    expect(screen.getByRole("heading")).toHaveAttribute("tabindex", "-1");
  });

  it("volta a receber o foco quando a etapa muda", () => {
    const { rerender } = render(<FocusHeading focusKey="a">Arquivos</FocusHeading>);
    screen.getByRole("heading").blur();

    rerender(<FocusHeading focusKey="b">Resultado</FocusHeading>);

    expect(screen.getByRole("heading", { name: "Resultado" })).toHaveFocus();
  });

  it("não tira o foco de um alerta que a moldura marcou como dono do foco inicial", () => {
    render(
      <div role="alert" tabIndex={-1} data-holds-initial-focus="">
        Não foi possível entrar.
      </div>,
    );
    screen.getByRole("alert").focus();

    // A tela chega depois, num chunk lazy, ao lado do alerta que já está na página.
    render(<FocusHeading>Oficina</FocusHeading>);

    expect(screen.getByRole("alert")).toHaveFocus();
  });
});
