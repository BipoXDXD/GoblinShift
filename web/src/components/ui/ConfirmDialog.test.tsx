import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef, useState, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { axeViolations } from "../../test/axe";
import { forceClose } from "../../test/dialog";
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

/**
 * Quem chama fecha e escolhe o destino do foco no mesmo render, sem esperar o commit (sem
 * `flushSync`): o diálogo é o único a mover o foco no fechamento.
 */
function DestinationHarness(): ReactElement {
  const [open, setOpen] = useState(false);
  const [toHeading, setToHeading] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  return (
    <>
      <h2 ref={heading} tabIndex={-1}>
        Chaves
      </h2>
      <button
        type="button"
        onClick={() => {
          setToHeading(false);
          setOpen(true);
        }}
      >
        Abrir
      </button>
      <button
        type="button"
        onClick={() => {
          setToHeading(true);
        }}
      >
        Resposta chegou
      </button>
      <ConfirmDialog
        open={open}
        title="Revogar a chave?"
        confirmLabel="Revogar chave"
        cancelLabel="Cancelar"
        returnFocusTo={toHeading ? heading : undefined}
        onConfirm={() => {
          setToHeading(true);
          setOpen(false);
        }}
        onCancel={() => {
          setOpen(false);
        }}
      >
        <p>Não dá para desfazer.</p>
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

  it("com a confirmação bloqueada mas sem pedido em curso, o Esc segue o navegador", async () => {
    const user = userEvent.setup();
    render(<Harness confirmBlocked />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    const notCanceled = fireEvent(
      screen.getByRole("dialog"),
      new Event("cancel", { cancelable: true }),
    );

    expect(notCanceled).toBe(true);
  });

  it("Cancelar devolve o foco a quem abriu", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(screen.getByRole("button", { name: "Abrir" })).toHaveFocus();
  });

  it("fechado pelo navegador (Esc), devolve o foco a quem abriu", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    forceClose(screen.getByRole("dialog"));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("button", { name: "Abrir" })).toHaveFocus();
  });

  it("fechado com destino, o foco termina no destino e não em quem abriu", async () => {
    const user = userEvent.setup();
    render(<DestinationHarness />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    await user.click(screen.getByRole("button", { name: "Revogar chave" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("heading", { name: "Chaves" })).toHaveFocus();
  });

  it("fechado pelo navegador antes da resposta, o destino que chega depois recebe o foco", async () => {
    const user = userEvent.setup();
    render(<DestinationHarness />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));
    forceClose(screen.getByRole("dialog"));
    expect(screen.getByRole("button", { name: "Abrir" })).toHaveFocus();

    fireEvent.click(screen.getByRole("button", { name: "Resposta chegou" }));

    expect(screen.getByRole("heading", { name: "Chaves" })).toHaveFocus();
  });

  it("um destino passado antes da primeira abertura não rouba o foco na montagem", () => {
    function Closed(): ReactElement {
      const heading = useRef<HTMLHeadingElement>(null);
      return (
        <>
          <h2 ref={heading} tabIndex={-1}>
            Chaves
          </h2>
          <ConfirmDialog
            open={false}
            title="Revogar a chave?"
            confirmLabel="Revogar chave"
            cancelLabel="Cancelar"
            returnFocusTo={heading}
            onConfirm={vi.fn()}
            onCancel={vi.fn()}
          >
            <p>Não dá para desfazer.</p>
          </ConfirmDialog>
        </>
      );
    }

    render(<Closed />);

    expect(document.body).toHaveFocus();
  });

  it("sem destino novo, um render posterior não move o foco", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));
    await user.click(screen.getByRole("button", { name: "Cancelar" }));
    screen.getByRole("button", { name: "Abrir" }).blur();

    rerender(<Harness busy />);

    expect(document.body).toHaveFocus();
  });

  it("no DOM, Cancelar vem antes da confirmação", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    const buttons = within(screen.getByRole("dialog")).getAllByRole("button");

    expect(buttons.map((button) => button.textContent)).toEqual(["Cancelar", "Revogar chave"]);
  });

  it("nenhum contêiner dos botões inverte a ordem visual (*-reverse)", async () => {
    // O jsdom não faz layout: a prova de comportamento é o e2e de session.spec.ts a 375 px. Aqui
    // fica a guarda rápida contra um `*-reverse` em qualquer ancestral até o diálogo, que separa a
    // ordem visual da do Tab.
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));
    const dialog = screen.getByRole("dialog");

    const classes: string[] = [];
    for (
      let node = screen.getByRole("button", { name: "Cancelar" }).parentElement;
      node !== null && node !== dialog;
      node = node.parentElement
    ) {
      classes.push(node.className);
    }

    expect(classes.join(" ")).not.toMatch(/reverse/);
  });

  it("não tem violações do axe aberto", async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness withField />);
    await user.click(screen.getByRole("button", { name: "Abrir" }));

    expect(await axeViolations(container)).toEqual([]);
  });
});
