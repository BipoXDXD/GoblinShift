import type { QueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { ME_QUERY_KEY } from "./api";

/** Abre um BroadcastChannel; nos testes, um canal em memória. */
export type ChannelFactory = (name: string) => BroadcastChannel;

/** O canal em que as abas da mesma origem avisam que a sessão acabou. */
export const SESSION_CHANNEL = "goblinshift-sessao";

const signedOutMessage = z.object({ type: z.literal("signed-out") });

function browserChannel(name: string): BroadcastChannel {
  return new globalThis.BroadcastChannel(name);
}

/**
 * O canal da sessão, ou `null` quando o navegador não tem BroadcastChannel (ou recusa abrir).
 * Sem canal, as outras abas só notam a saída no próximo 401, como antes.
 */
export function openSessionChannel(
  create: ChannelFactory = browserChannel,
): BroadcastChannel | null {
  try {
    return create(SESSION_CHANNEL);
  } catch {
    return null;
  }
}

/**
 * Avisa as outras abas que a sessão acabou. O aviso é o melhor esforço: canal fechado não impede
 * o logout desta aba, e as outras ainda notam no próximo 401.
 */
export function announceSignedOut(channel: BroadcastChannel | null): void {
  try {
    channel?.postMessage({ type: "signed-out" });
  } catch {
    // Canal fechado (InvalidStateError): segue sem o aviso.
  }
}

/**
 * Ouve o aviso das outras abas e põe `null` no /me, sem pedir de novo: o Clear-Site-Data já
 * apagou o cookie para a origem toda. O `watchAccountChange` vê a conta virar `null`, esquece os
 * dados e as telas abertas mostram o convite para entrar. Devolve o `unsubscribe`.
 */
export function watchSignedOutElsewhere(
  queryClient: QueryClient,
  channel: BroadcastChannel | null,
): () => void {
  if (channel === null) {
    return () => undefined;
  }
  const signOutHere = async (): Promise<void> => {
    // Um /me em voo voltaria com 200 depois do aviso e gravaria a conta antiga por cima do `null`.
    // O `await` garante a ordem cancelar → gravar sem depender de como o TanStack reverte o estado
    // (na 5.107 a reversão é síncrona, e o `null` gravado à mão também vira o estado de reversão).
    await queryClient.cancelQueries({ queryKey: ME_QUERY_KEY });
    queryClient.setQueryData(ME_QUERY_KEY, null);
  };
  const onMessage = (event: MessageEvent<unknown>): void => {
    if (signedOutMessage.safeParse(event.data).success) {
      void signOutHere();
    }
  };
  channel.addEventListener("message", onMessage);
  return () => {
    channel.removeEventListener("message", onMessage);
  };
}
