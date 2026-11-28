import { useEffect, useRef } from "react";

import { filesFromClipboard } from "./pastedFiles";

/**
 * Ctrl/⌘+V em qualquer ponto da página entrega os arquivos da área de transferência, enquanto
 * `enabled`. Sem arquivo na colagem o evento segue intocado, e o texto cai no campo de sempre.
 * Também fica de fora a colagem dentro de um diálogo aberto (a Bancada atrás dele está inerte) e a
 * colagem num campo de texto quando a área de transferência traz texto junto: quem cola ali quer o
 * texto. O listener sai junto com a Bancada.
 */
export function usePasteFiles(enabled: boolean, onFiles: (files: readonly File[]) => void): void {
  const pastedCount = useRef(0);
  const latestOnFiles = useRef(onFiles);
  useEffect(() => {
    latestOnFiles.current = onFiles;
  });

  useEffect(() => {
    if (!enabled) {
      return;
    }
    const onPaste = (event: ClipboardEvent): void => {
      const data = event.clipboardData;
      if (data === null || !Array.from(data.types).includes("Files")) {
        return;
      }
      if (insideOpenDialog(event.target) || (editable(event.target) && carriesText(data))) {
        return;
      }
      const files = filesFromClipboard(data, () => ++pastedCount.current);
      if (files.length === 0) {
        return;
      }
      event.preventDefault();
      latestOnFiles.current(files);
    };
    document.addEventListener("paste", onPaste);
    return () => {
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
