import { useEffect, useRef, useState } from "react";

import { peekShared, takeSharedFiles } from "./sharedInbox";

export interface SharedFiles {
  /** O compartilhamento tinha vencido ou já fora usado. */
  readonly expired: boolean;
  /** Há compartilhamento e a caixa de entrada ainda não disse o que chegou nele. */
  readonly reading: boolean;
  /** Nomes dos arquivos que esperam a decisão; eles seguem na caixa de entrada até lá. */
  readonly pending: readonly string[];
  /** A pessoa aceitou: os arquivos saem da caixa e vão para o lote. */
  readonly accept: () => Promise<void>;
  /** A pessoa recusou: os arquivos saem da caixa sem envio. */
  readonly discard: () => Promise<void>;
}

/**
 * Espia os nomes do compartilhamento `id` e espera a pessoa decidir (ADR 064): qualquer site pode
 * fazer o POST do `share_target`, então a Bancada nunca envia o que chegou por ele sem um clique.
 * Até a decisão os arquivos ficam na caixa, e recarregar a página não os perde. Só roda onde a
 * Bancada já abriu, isto é, com sessão: sem login, eles esperam a volta do "Entrar".
 *
 * A guarda por `ref` cobre o efeito duplo do StrictMode, e a leitura é assíncrona, sem `setState`
 * síncrono no efeito.
 */
export function useSharedFiles(
  id: string | null,
  onAccept: (files: readonly File[]) => void,
): SharedFiles {
  const [expired, setExpired] = useState(false);
  const [pending, setPending] = useState<readonly string[]>([]);
  const [readId, setReadId] = useState<string | null>(null);
  const peeked = useRef<string | null>(null);
  useEffect(() => {
    if (id === null || peeked.current === id) {
      return;
    }
    peeked.current = id;
    void peekShared(id)
      .catch(() => "expired" as const)
      .then((names) => {
        setReadId(id);
        if (names === "expired") {
          setExpired(true);
        } else {
          setPending(names);
        }
      });
  }, [id]);

  const take = async (): Promise<readonly File[] | "expired"> => {
    setPending([]);
    return id === null ? "expired" : takeSharedFiles(id).catch(() => "expired" as const);
  };
  return {
    expired,
    reading: id !== null && readId !== id,
    pending,
    accept: async () => {
      const files = await take();
      if (files === "expired") {
        setExpired(true);
      } else {
        onAccept(files);
      }
    },
    discard: async () => {
      await take();
    },
  };
}
