import { useEffect, useId, useRef, type ReactElement, type ReactNode } from "react";

import { Button } from "./Button";

/** Marca o elemento que recebe o foco ao abrir; o campo de quem chama vem antes de Cancelar. */
const INITIAL_FOCUS = "[data-initial-focus]";

interface ConfirmDialogProps {
  readonly open: boolean;
  readonly title: string;
  readonly children: ReactNode;
  readonly confirmLabel: string;
  readonly cancelLabel: string;
  readonly onConfirm: () => void;
  /** Cancelar, Esc ou o fechamento pelo navegador. */
  readonly onCancel: () => void;
  /** A confirmação fica `aria-disabled` (digitação pendente, pedido em curso). */
  readonly confirmBlocked?: boolean;
  /** Vermelho só aqui, onde a ação destrutiva é a primária (RUI cap. 2). */
  readonly destructive?: boolean;
}

/**
 * Confirmação em `<dialog>` modal nativo: o navegador prende o foco, fecha no Esc, deixa o resto da
 * página inerte e devolve o foco a quem abriu. O foco inicial vai para Cancelar, a escolha que não
 * estraga nada, ou para o campo marcado com `data-initial-focus` nos filhos.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
  confirmBlocked = false,
  destructive = true,
}: ConfirmDialogProps): ReactElement {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const bodyId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (element === null) {
      return;
    }
    if (open && !element.open) {
      element.showModal();
      // O primeiro marcado na ordem do documento: o campo da confirmação digitada, se houver,
      // senão Cancelar. Explícito porque o foco padrão do showModal muda entre navegadores.
      element.querySelector<HTMLElement>(INITIAL_FOCUS)?.focus();
    } else if (!open && element.open) {
      element.close();
    }
  }, [open]);
  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onClose={onCancel}
      className="m-auto w-full max-w-md rounded-lg bg-surface-raised p-6 text-fg shadow-dialog"
    >
      <div className="flex flex-col gap-4">
        <h2 id={titleId} className="font-display text-xl font-semibold">
          {title}
        </h2>
        <div id={bodyId} className="flex flex-col gap-4 text-fg-muted">
          {children}
        </div>
        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
          <Button type="button" variant="secondary" data-initial-focus="" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={destructive ? "destructive" : "primary"}
            aria-disabled={confirmBlocked}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
