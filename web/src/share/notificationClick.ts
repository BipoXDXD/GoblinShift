// Clique numa notificação de término (aviso de término, ADR 064): foca a aba que já mostra a
// execução ou abre `data.url`. Sem `self`: o service worker passa o `clients` dele, e o teste um
// falso.

import { LOCAL_PATH } from "../app/localPath";

interface NotificationWindow {
  readonly url: string;
  focus: () => Promise<unknown>;
}

/** O pedaço de `Clients` (service worker) que o clique usa. */
export interface NotificationClients {
  matchAll: (options: {
    readonly type: "window";
    readonly includeUncontrolled: boolean;
  }) => Promise<readonly NotificationWindow[]>;
  openWindow: (url: string) => Promise<unknown>;
}

/**
 * Foca a aba aberta em `data.url` ou abre uma nova. `data.url` fora deste site (outra origem,
 * `//host`, ausente) abre a Oficina: a notificação nunca leva para outro site.
 */
export async function openNotificationTarget(
  clients: NotificationClients,
  data: unknown,
  origin: string,
): Promise<void> {
  const target = localTarget(data, origin) ?? new URL("/", origin);
  const windows = await clients.matchAll({ type: "window", includeUncontrolled: true });
  const open = windows.find((client) => samePage(client.url, target));
  if (open !== undefined) {
    await open.focus();
    return;
  }
  await clients.openWindow(target.href);
}

function localTarget(data: unknown, origin: string): URL | null {
  if (typeof data !== "object" || data === null || !("url" in data)) {
    return null;
  }
  const { url } = data;
  if (typeof url !== "string" || !LOCAL_PATH.test(url)) {
    return null;
  }
  const target = new URL(url, origin);
  return target.origin === origin ? target : null;
}

function samePage(clientUrl: string, target: URL): boolean {
  const client = new URL(clientUrl);
  return client.origin === target.origin && client.pathname === target.pathname;
}
