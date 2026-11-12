import { useEffect, useId, useRef, type ReactElement, type ReactNode, type RefObject } from "react";

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
  /**
   * Cancelar, Esc ou o fechamento pelo navegador. Quem chama fecha sempre (`open` false): o
   * elemento já pode estar fechado, e o estado não pode divergir dele.
   */
  readonly onCancel: () => void;
  /** A confirmação fica `aria-disabled` (pedido em curso). */
  readonly confirmBlocked?: boolean;
  /** Pedido em curso: o Esc não fecha, para a resposta aparecer no diálogo. */
  readonly busy?: boolean;
  /** Vermelho só aqui, onde a ação destrutiva é a primária (RUI cap. 2). */
  readonly destructive?: boolean;
  /**
   * Destino do foco quando quem abriu some ou deixa de ser o lugar certo (WCAG 2.4.3). Quem chama o
   * passa no mesmo render em que fecha (`open` false), e o diálogo o foca logo depois de fechar.
   * Se o navegador já fechou o diálogo, o destino que chegar depois recebe o foco. Sem ele, o foco
   * volta a quem abriu, como no `<dialog>` nativo.
   */
  readonly returnFocusTo?: RefObject<HTMLElement | null> | undefined;
}

/**
 * Confirmação em `<dialog>` modal nativo: o navegador prende o foco, fecha no Esc, deixa o resto da
 * página inerte e devolve o foco a quem abriu. O foco inicial vai para Cancelar, a escolha que não
 * estraga nada, ou para o campo marcado com `data-initial-focus` nos filhos.
 *
 * O diálogo é o único dono do foco ao abrir e ao fechar: quem chama não move o foco no fechamento,
 * passa `returnFocusTo`. Assim o fechamento, que roda depois do commit, não devolve o foco a quem
 * abriu por cima de um destino já aplicado.
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
  busy = false,
  destructive = true,
  returnFocusTo,
}: ConfirmDialogProps): ReactElement {
  const dialog = useRef<HTMLDialogElement>(null);
  // Sem isto, um destino passado antes da primeira abertura roubaria o foco na montagem.
  const opened = useRef(false);
  const opener = useRef<Element | null>(null);
  const titleId = useId();
  const bodyId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (element === null) {
      return;
    }
    if (open) {
      if (!element.open) {
        opener.current = document.activeElement;
        element.showModal();
        opened.current = true;
        // O primeiro marcado na ordem do documento: o campo da confirmação digitada, se houver,
        // senão Cancelar. Explícito porque o foco padrão do showModal muda entre navegadores.
        element.querySelector<HTMLElement>(INITIAL_FOCUS)?.focus();
      }
      return;
    }
    const closing = element.open;
    if (closing) {
      // O close devolve o foco a quem abriu; o destino, se houver, vem logo depois e prevalece.
      element.close();
    }
    // Destino que chega depois de o navegador fechar o diálogo só leva o foco se a pessoa não o
    // moveu: ele ainda está em quem abriu ou caiu no <body> porque quem abriu saiu do DOM.
    if (opened.current && (closing || isFocusUnmoved(opener.current))) {
      returnFocusTo?.current?.focus();
    }
  }, [open, returnFocusTo]);
  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(event) => {
        // O Chrome ainda força o fechamento no Esc seguinte; aí o onClose avisa quem chamou.
        if (busy) {
          event.preventDefault();
        }
      }}
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
        {/* No celular, a saída segura fica em cima de propósito, contra a convenção do iOS e do
            Material (ação em cima): a ordem visual tem de ser a do Tab (WCAG 2.4.3 e 1.3.2), e o
            foco inicial está no primeiro da pilha (o campo, se houver, senão Cancelar). Não voltar
            a um *-reverse. Os dois botões ocupam a largura toda pelo stretch do flex-col. */}
        <div className="flex flex-col gap-3 sm:flex-row sm:justify-end">
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

function isFocusUnmoved(opener: Element | null): boolean {
  const active = document.activeElement;
  return active === null || active === document.body || active === opener;
}
