import { notifyManager } from "@tanstack/react-query";
import { act } from "@testing-library/react";
import { onTestFinished, vi } from "vitest";

/** Voltas do laço de eventos antes de desistir de uma espera com o relógio falso. */
const MAX_TURNS = 500;
/** O `setTimeout` de verdade, guardado antes de o relógio falso o trocar. */
const realSetTimeout = globalThis.setTimeout;

/**
 * Relógio falso só para o `setTimeout` do atraso dos anúncios. Ligue depois de a tela carregar:
 * o `findBy`/`waitFor` e o user-event do Testing Library esperam um `setTimeout(0)` que ficaria
 * parado, então daí em diante use `fireEvent` e `untilFakeClock`. O TanStack Query entrega as
 * notificações num `setTimeout(0)`: aqui elas saem numa microtask.
 */
export function useAnnounceClock(): void {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  notifyManager.setScheduler(queueMicrotask);
  onTestFinished(() => {
    notifyManager.setScheduler((callback) => {
      setTimeout(callback, 0);
    });
    vi.useRealTimers();
  });
}

/** Espera a asserção passar girando o laço de eventos real, sem andar o relógio falso. */
export async function untilFakeClock(assertion: () => void): Promise<void> {
  for (let turn = 0; turn < MAX_TURNS; turn += 1) {
    if (passes(assertion)) {
      return;
    }
    await act(async () => {
      await new Promise((resolve) => {
        realSetTimeout(resolve, 0);
      });
    });
  }
  assertion();
}

function passes(assertion: () => void): boolean {
  try {
    assertion();
    return true;
  } catch {
    // Ainda não: a próxima volta do laço tenta de novo; a última volta deixa o erro subir.
    return false;
  }
}
