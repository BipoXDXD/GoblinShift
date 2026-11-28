import { useEffect, useState } from "react";

import { TEXT } from "../text";
import type { RunStatus } from "./api";
import { isTerminalStatus } from "./polling";

/** Términos que merecem aviso; a cancelada fica de fora porque foi a pessoa que cancelou. */
export type SignaledStatus = "succeeded" | "succeeded_with_warnings" | "failed";

const SIGNALED: ReadonlySet<RunStatus> = new Set<SignaledStatus>([
  "succeeded",
  "succeeded_with_warnings",
  "failed",
]);

function isSignaled(status: RunStatus): status is SignaledStatus {
  return SIGNALED.has(status);
}

/**
 * O término a avisar, ou `null`. Só a transição de um estado não terminal para um dos três avisa: a
 * primeira leitura (`previous` nulo) é a execução que já abriu terminada, e nada mudou enquanto a
 * pessoa esperava.
 */
export function signaledStatus(previous: RunStatus | null, next: RunStatus): SignaledStatus | null {
  if (previous === null || isTerminalStatus(previous) || !isSignaled(next)) {
    return null;
  }
  return next;
}

/**
 * Prefixa o título da aba com o término quando ele chega com a aba escondida ("✓ Concluída ·
 * Execução · GoblinShift") e devolve o título da rota quando a aba volta a ficar visível. Com a aba
 * visível, a própria tela já mostra o término e o título fica como está.
 *
 * Devolve o término sinalizado (o mesmo valor depois da transição), para o aviso do navegador.
 */
export function useCompletionTitle(status: RunStatus | undefined): SignaledStatus | null {
  const [tracked, setTracked] = useState<{
    previous: RunStatus | null;
    signal: SignaledStatus | null;
  }>({ previous: null, signal: null });
  if (status !== undefined && status !== tracked.previous) {
    // Ajuste de estado durante a renderização, como em useRunPolling.
    setTracked({
      previous: status,
      signal: signaledStatus(tracked.previous, status) ?? tracked.signal,
    });
  }
  const { signal } = tracked;

  useEffect(() => {
    if (signal === null || document.visibilityState !== "hidden") {
      return undefined;
    }
    const routeTitle = document.title;
    document.title = `${TEXT.run.completion.title[signal]} · ${routeTitle}`;
    const restore = (): void => {
      if (document.visibilityState === "visible") {
        document.title = routeTitle;
        document.removeEventListener("visibilitychange", restore);
      }
    };
    document.addEventListener("visibilitychange", restore);
    // Ao sair da tela, o título da rota nova vem do Layout; restaurar aqui o sobrescreveria.
    return () => {
      document.removeEventListener("visibilitychange", restore);
    };
  }, [signal]);

  return signal;
}
