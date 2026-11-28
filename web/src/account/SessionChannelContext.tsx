import type { QueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { openSessionChannel, watchSignedOutElsewhere, type ChannelFactory } from "./sessionSignal";

const SessionChannelContext = createContext<BroadcastChannel | null>(null);

interface SessionChannelProviderProps {
  readonly channel: BroadcastChannel | null;
  readonly children: ReactNode;
}

/** Dá às telas o canal da sessão entre abas (sessionSignal.ts); `null` é navegador sem o canal. */
export function SessionChannelProvider({
  channel,
  children,
}: SessionChannelProviderProps): ReactElement {
  return <SessionChannelContext value={channel}>{children}</SessionChannelContext>;
}

/** O canal da sessão, ou `null` sem canal (ou fora do provider): avisar vira nada. */
export function useSessionChannel(): BroadcastChannel | null {
  return useContext(SessionChannelContext);
}

/**
 * Abre o canal da sessão enquanto o app está montado, liga nele o aviso das outras abas
 * (`watchSignedOutElsewhere`) e o fecha no unmount. O canal nasce no efeito, e não no render: no
 * Strict Mode o efeito roda, limpa e roda de novo, e um canal criado no render ficaria fechado.
 * Devolve o canal aberto, para o `SessionChannelProvider`; `null` até o efeito rodar ou sem canal.
 */
export function useOpenSessionChannel(
  queryClient: QueryClient,
  create?: ChannelFactory,
): BroadcastChannel | null {
  const [channel, setChannel] = useState<BroadcastChannel | null>(null);
  useEffect(() => {
    const opened = openSessionChannel(create);
    const stopWatching = watchSignedOutElsewhere(queryClient, opened);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- o canal é recurso externo, aberto e fechado pelo efeito; o estado só o entrega às telas
    setChannel(opened);
    return () => {
      stopWatching();
      opened?.close();
      setChannel(null);
    };
  }, [queryClient, create]);
  return channel;
}
