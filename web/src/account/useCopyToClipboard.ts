import { useState } from "react";

/** `idle` antes do primeiro clique; depois, o resultado da última cópia. */
export type CopyState = "idle" | "copied" | "failed";

interface CopyToClipboard {
  readonly state: CopyState;
  readonly copy: (text: string) => Promise<void>;
}

/**
 * Copia um texto para a área de transferência e guarda o resultado, para a tela anunciar o sucesso
 * (região polida) ou a falha (região assertiva). Usado pela chave nova e pelo identificador da conta.
 */
export function useCopyToClipboard(): CopyToClipboard {
  const [state, setState] = useState<CopyState>("idle");
  const copy = async (text: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      // Sem permissão ou sem a API de área de transferência: o texto continua selecionável.
      setState("failed");
    }
  };
  return { state, copy };
}
