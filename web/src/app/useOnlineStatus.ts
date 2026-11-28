import { onlineManager } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";

/**
 * Se o navegador está com conexão, pelo `onlineManager` do TanStack Query: é ele que pausa queries
 * e mutations quando a conexão cai, então a tela diz o mesmo que o cache vê. Sem `window` (render
 * no servidor), parte de online, como o próprio `onlineManager`.
 */
export function useOnlineStatus(): boolean {
  return useSyncExternalStore(
    (onChange) => onlineManager.subscribe(onChange),
    () => onlineManager.isOnline(),
    () => true,
  );
}
