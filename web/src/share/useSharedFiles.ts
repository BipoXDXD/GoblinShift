import { useEffect, useRef, useState } from "react";

import { takeSharedFiles } from "./sharedInbox";

/**
 * Tira da caixa de entrada os arquivos do compartilhamento `id`, uma vez só, e os entrega a
 * `onFiles`. Devolve se o compartilhamento tinha vencido ou já fora usado. Só roda onde a Bancada
 * já abriu, isto é, com sessão: sem login, os arquivos esperam na caixa até a volta do "Entrar".
 *
 * A guarda por `ref` cobre o efeito duplo do StrictMode, e a entrega é assíncrona: ela chega
 * depois da montagem dupla, sem cair na desmontagem simulada que cancela os envios do lote.
 */
export function useSharedFiles(
  id: string | null,
  onFiles: (files: readonly File[]) => void,
): boolean {
  const [expired, setExpired] = useState(false);
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
          onFiles(result);
        }
      });
  }, [id, onFiles]);
  return expired;
}
