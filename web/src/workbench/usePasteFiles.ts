import { useEffect, useRef } from "react";

import type { FolderPick } from "../upload/folderFiles";
import { useFolderRead } from "../upload/useFolderRead";
import { filesFromClipboard } from "./pastedFiles";

interface PasteTargets {
  /** Teto de arquivos de uma pasta colada (o de entradas por execução). */
  readonly maxFiles: number;
  readonly onFiles: (files: readonly File[]) => void;
  /** Pasta colada, lida em profundidade e conferida contra `maxFiles`. */
  readonly onFolder: (pick: FolderPick) => void;
  /** A Bancada ficou ocupada enquanto a pasta colada era lida: a colagem foi descartada. */
  readonly onCanceled: () => void;
}

/**
 * Ctrl/⌘+V em qualquer ponto da página entrega os arquivos da área de transferência, enquanto
 * `enabled`. Sem arquivo na colagem o evento segue intocado, e o texto cai no campo de sempre.
 * Também fica de fora a colagem dentro de um diálogo aberto (a Bancada atrás dele está inerte) e a
 * colagem num campo de texto quando a área de transferência traz texto junto: quem cola ali quer o
 * texto. Uma pasta copiada no gerenciador de arquivos chega como pasta, nunca como o `File` de 0 B
 * que o navegador também põe na colagem. O listener sai junto com a Bancada, e a leitura da pasta
 * é cancelada junto. Se a Bancada fica ocupada (`enabled` cai) no meio da leitura, a colagem é
 * descartada e `onCanceled` avisa, em vez de silêncio.
 */
export function usePasteFiles(enabled: boolean, targets: PasteTargets): void {
  const pastedCount = useRef(0);
  const latest = useRef(targets);
  const latestEnabled = useRef(enabled);
  useEffect(() => {
    latest.current = targets;
    latestEnabled.current = enabled;
  });
  const folderRead = useFolderRead((pick) => {
    // A leitura pode terminar antes de o efeito abaixo cancelá-la.
    if (latestEnabled.current) {
      latest.current.onFolder(pick);
    } else {
      latest.current.onCanceled();
    }
  });

  useEffect(() => {
    if (!enabled && folderRead.cancel()) {
      latest.current.onCanceled();
    }
  }, [enabled, folderRead]);

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
      if (folderRead.start(data.items, latest.current.maxFiles)) {
        event.preventDefault();
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
      document.removeEventListener("paste", onPaste);
    };
  }, [enabled, folderRead]);
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
