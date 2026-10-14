import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button, buttonClassName } from "./Button";

describe("Button", () => {
  it("tem o type pedido e o nome acessível do texto", () => {
    render(
      <Button type="submit" variant="primary">
        Executar
      </Button>,
    );

    expect(screen.getByRole("button", { name: "Executar" })).toHaveAttribute("type", "submit");
  });

  it("chama onClick quando ativo", async () => {
    const onClick = vi.fn();
    render(
      <Button type="button" variant="secondary" onClick={onClick}>
        Cancelar
      </Button>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("com aria-disabled continua focável, mas não age nem envia o formulário", async () => {
    const onClick = vi.fn();
    const onSubmit = vi.fn((event: { preventDefault: () => void }) => {
      event.preventDefault();
    });
    render(
      <form onSubmit={onSubmit}>
        <Button type="submit" variant="primary" aria-disabled onClick={onClick}>
          Executar
        </Button>
      </form>,
    );
    const button = screen.getByRole("button", { name: "Executar" });

    await userEvent.click(button);

    expect(onClick).not.toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
    expect(button).toHaveAttribute("aria-disabled", "true");
    button.focus();
    expect(button).toHaveFocus();
  });
});

describe("buttonClassName", () => {
  it.each(["primary", "secondary", "tertiary", "destructive"] as const)(
    "%s garante o alvo de toque de 44 px",
    (variant) => {
      expect(buttonClassName({ variant }).split(" ")).toContain("min-h-11");
    },
  );

  it("só o primário e o destrutivo são sólidos", () => {
    expect(buttonClassName({ variant: "primary" })).toContain("bg-primary");
    expect(buttonClassName({ variant: "destructive" })).toContain("bg-destructive");
    expect(buttonClassName({ variant: "secondary" })).not.toMatch(/\bbg-(primary|destructive)\b/);
    expect(buttonClassName({ variant: "tertiary" })).not.toMatch(/\bbg-(primary|destructive)\b/);
  });
});
