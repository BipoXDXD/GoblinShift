import { useEffect, useRef, useState } from "react";

import { takeSharedFiles } from "./sharedInbox";

export interface SharedFiles {
  /** O compartilhamento tinha vencido ou já fora usado. */
  readonly expired: boolean;
  /** Arquivos recebidos que esperam a pessoa decidir; nada foi enviado ainda. */
  readonly pending: readonly File[];
  /** A pessoa aceitou: os arquivos vão para o lote. */
  readonly accept: () => void;
  readonly discard: () => void;
}

/**
 * Tira da caixa de entrada os arquivos do compartilhamento `id`, uma vez só, e os segura até a
 * pessoa decidir (ADR 064): qualquer site pode fazer o POST do `share_target`, então a Bancada
 * nunca envia o que chegou por ele sem um clique. Só roda onde a Bancada já abriu, isto é, com
 * sessão: sem login, os arquivos esperam na caixa até a volta do "Entrar".
 *
 * A guarda por `ref` cobre o efeito duplo do StrictMode, e a leitura é assíncrona, sem `setState`
 * síncrono no efeito.
 */
export function useSharedFiles(
  id: string | null,
  onAccept: (files: readonly File[]) => void,
): SharedFiles {
  const [expired, setExpired] = useState(false);
  const [pending, setPending] = useState<readonly File[]>([]);
  const taken = useRef<string | null>(null);
  useEffect(() => {
    if (id === null || taken.current === id) {
      return;
    }
    taken.current = id;
    void takeSharedFiles(id)
      .catch(() => "expired" as const)
      .then((result) => {
        if (result === "expired") {
          setExpired(true);
        } else {
          setPending(result);
        }
      });
  }, [id]);
  return {
    expired,
    pending,
    accept: () => {
      onAccept(pending);
      setPending([]);
    },
    discard: () => {
      setPending([]);
    },
  };
}
