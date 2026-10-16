import { fireEvent } from "@testing-library/react";

/** O Esc chega ao `<dialog>` como `cancel`; `true` quando o diálogo o impediu e ficou aberto. */
export function escapeIsBlocked(dialog: HTMLElement): boolean {
  return !fireEvent(dialog, new Event("cancel", { cancelable: true }));
}

/** O Chrome fecha o diálogo no segundo Esc mesmo com o `cancel` impedido. */
export function forceClose(dialog: HTMLElement): void {
  if (!(dialog instanceof HTMLDialogElement)) {
    throw new Error("o diálogo não é um <dialog>");
  }
  dialog.close();
}

/** Um pedido segurado pelo teste até `answer`. */
export function heldResponse(): {
  readonly handler: () => Promise<Response>;
  readonly answer: (response: Response) => void;
} {
  let resolveHeld: (response: Response) => void = () => {
    throw new Error("o pedido segurado ainda não foi feito");
  };
  return {
    handler: () =>
      new Promise<Response>((resolve) => {
        resolveHeld = resolve;
      }),
    answer: (response) => {
      resolveHeld(response);
    },
  };
}
