import { useEffect, useRef } from "react";

import { collectEntries, folderEntries, type FolderPick } from "./folderFiles";
import { filesFromClipboard } from "./pastedFiles";

interface PasteTargets {
  /** Teto de arquivos de uma pasta colada (o de entradas por execução). */
  readonly maxFiles: number;
  readonly onFiles: (files: readonly File[]) => void;
  /** Pasta colada, lida em profundidade e conferida contra `maxFiles`. */
  readonly onFolder: (pick: FolderPick) => void;
}

/**
 * Ctrl/⌘+V em qualquer ponto da página entrega os arquivos da área de transferência, enquanto
 * `enabled`. Sem arquivo na colagem o evento segue intocado, e o texto cai no campo de sempre.
 * Também fica de fora a colagem dentro de um diálogo aberto (a Bancada atrás dele está inerte) e a
 * colagem num campo de texto quando a área de transferência traz texto junto: quem cola ali quer o
 * texto. Uma pasta copiada no gerenciador de arquivos chega como pasta, nunca como o `File` de 0 B
 * que o navegador também põe na colagem. O listener sai junto com a Bancada, e a pasta que termina
 * de ser lida depois disso não entrega nada.
 */
export function usePasteFiles(enabled: boolean, targets: PasteTargets): void {
  const pastedCount = useRef(0);
  const latest = useRef(targets);
  useEffect(() => {
    latest.current = targets;
  });

  useEffect(() => {
    if (!enabled) {
      return;
    }
    let active = true;
    const onPaste = (event: ClipboardEvent): void => {
      const data = event.clipboardData;
      if (data === null || !Array.from(data.types).includes("Files")) {
        return;
      }
      if (insideOpenDialog(event.target) || (editable(event.target) && carriesText(data))) {
        return;
      }
      // Os itens só valem durante o evento: as entradas saem daqui antes de qualquer espera.
      const roots = folderEntries(data.items);
      if (roots !== null) {
        event.preventDefault();
        void collectEntries(roots, latest.current.maxFiles).then((pick) => {
          if (active) {
            latest.current.onFolder(pick);
          }
        });
        return;
      }
      const files = filesFromClipboard(data, () => ++pastedCount.current);
      if (files.length === 0) {
        return;
      }
      event.preventDefault();
      latest.current.onFiles(files);
    };
    document.addEventListener("paste", onPaste);
    return () => {
      active = false;
      document.removeEventListener("paste", onPaste);
    };
  }, [enabled]);
}

function insideOpenDialog(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest("dialog[open]") !== null;
}

function editable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target.isContentEditable
  );
}

function carriesText(data: DataTransfer): boolean {
  return Array.from(data.types).includes("text/plain") && data.getData("text/plain") !== "";
}
