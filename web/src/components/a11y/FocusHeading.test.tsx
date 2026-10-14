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
});
