import { useCallback, useEffect, useMemo, useRef } from "react";

import { collectEntries, folderEntries, type FolderPick } from "./folderFiles";

/** O mínimo de `DataTransfer.items` que a leitura de pasta usa. */
type FolderItems = Parameters<typeof folderEntries>[0];

export interface FolderRead {
  /**
   * Começa a ler as pastas de `items`, se houver alguma, e devolve `true`; sem pasta, devolve
   * `false` e quem chamou segue com os arquivos soltos. Precisa rodar dentro do evento: depois
   * dele o navegador esvazia a lista de itens.
   */
  readonly start: (items: FolderItems, maxFiles: number) => boolean;
  /** Cancela as leituras em curso; `true` se havia alguma. */
  readonly cancel: () => boolean;
}

/**
 * Leitura de pasta solta ou colada, com o resultado entregue ao `onPick` mais recente. Sair da
 * tela cancela a leitura (`AbortSignal`), e uma leitura cancelada nunca entrega nada: nada de
 * envio a partir de uma tela que já fechou.
 */
export function useFolderRead(onPick: (pick: FolderPick) => void): FolderRead {
  const latestOnPick = useRef(onPick);
  useEffect(() => {
    latestOnPick.current = onPick;
  });
  const reads = useRef(new Set<AbortController>());

  const cancel = useCallback((): boolean => {
    const pending = reads.current.size > 0;
    for (const controller of reads.current) {
      controller.abort();
    }
    reads.current.clear();
    return pending;
  }, []);

  // Sair da tela cancela as leituras em curso.
  useEffect(
    () => () => {
      cancel();
    },
    [cancel],
  );

  const start = useCallback((items: FolderItems, maxFiles: number): boolean => {
    const roots = folderEntries(items);
    if (roots === null) {
      return false;
    }
    const controller = new AbortController();
    reads.current.add(controller);
    void collectEntries(roots, maxFiles, controller.signal).then((pick) => {
      reads.current.delete(controller);
      if (pick !== null && !controller.signal.aborted) {
        latestOnPick.current(pick);
      }
    });
    return true;
  }, []);

  return useMemo(() => ({ start, cancel }), [start, cancel]);
}
