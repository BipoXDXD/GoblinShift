import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import { ConfirmDialog } from "./ConfirmDialog";

function Harness({
  onConfirm = vi.fn(),
  confirmBlocked = false,
  busy = false,
  withField = false,
}: {
  readonly onConfirm?: () => void;
  readonly confirmBlocked?: boolean;
  readonly busy?: boolean;
  readonly withField?: boolean;
}): ReactElement {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => {
          setOpen(true);
        }}
      >
        Abrir
      </button>
      <ConfirmDialog
        open={open}
        title="Revogar a chave?"
        confirmLabel="Revogar chave"
        cancelLabel="Cancelar"
        confirmBlocked={confirmBlocked}
        busy={busy}
        onConfirm={onConfirm}
        onCancel={() => {
          setOpen(false);
        }}
      >
        <p>Não dá para desfazer.</p>
        {withField && (
          <label>
            Frase
            <input data-initial-focus="" />
          </label>
        )}
      </ConfirmDialog>
    </>
  );
}

describe("ConfirmDialog", () => {
  it("fechado, não aparece para o leitor de tela", () => {
    render(<Harness />);

    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("abre com título e descrição e o foco em Cancelar", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "Abrir" }));

    const dialog = screen.getByRole("dialog", { name: "Revogar a chave?" });
    expect(dialog).toHaveAccessibleDescription(/Não dá para desfazer\./);
    expect(screen.getByRole("button", { name: "Cancelar" })).toHaveFocus();
  });

  it("com um campo marcado, o foco vai para o campo", async () => {
    const user = userEvent.setup();
    render(<Harness withField />);

    await user.click(screen.getByRole("button", { name: "Abrir" }));

    expect(screen.getByRole("textbox", { name: "Frase" })).toHaveFocus();
  });

  it("Cancelar fecha sem confirmar", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("confirmar chama a ação", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    await user.click(screen.getByRole("button", { name: "Revogar chave" }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("bloqueada, a confirmação continua no Tab mas não age", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} confirmBlocked />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    const confirm = screen.getByRole("button", { name: "Revogar chave" });
    await user.click(confirm);

    expect(confirm).toHaveAttribute("aria-disabled", "true");
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("com pedido em curso, o Esc (cancel nativo) não fecha o diálogo", async () => {
    const user = userEvent.setup();
    render(<Harness confirmBlocked busy />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    const notCanceled = fireEvent(
      screen.getByRole("dialog"),
      new Event("cancel", { cancelable: true }),
    );

    expect(notCanceled).toBe(false);
  });

  it("só com a confirmação bloqueada (digitação pendente), o Esc segue o navegador", async () => {
    const user = userEvent.setup();
    render(<Harness confirmBlocked />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    const notCanceled = fireEvent(
      screen.getByRole("dialog"),
      new Event("cancel", { cancelable: true }),
    );

    expect(notCanceled).toBe(true);
  });

  it("não tem violações do axe aberto", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness withField />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    expect(await axeViolations(container)).toEqual([]);
  });
});
