// Service worker do GoblinShift (ADR 062). Só duas coisas: receber o `POST /compartilhar` do
// `share_target` (manifest.webmanifest) e tratar o clique na notificação de término. Não guarda
// cache de página nem de asset e não intercepta nenhum outro pedido: um deploy novo vale na próxima
// navegação, como sem service worker (`Cache-Control: no-cache` no index.html).

import { openNotificationTarget } from "./share/notificationClick";
import { storeShared } from "./share/inbox";

declare const self: ServiceWorkerGlobalScope;

const SHARE_PATH = "/compartilhar";
/** Sem como guardar os arquivos (cota, corpo ilegível), a Oficina explica o motivo. */
const SHARE_UNAVAILABLE = "/?compartilhamento=indisponivel";

// Versão nova assume na hora: ela não tem estado nem cache para migrar.
self.addEventListener("install", () => {
  void self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (
    request.method !== "POST" ||
    url.origin !== self.location.origin ||
    url.pathname !== SHARE_PATH
  ) {
    return;
  }
  event.respondWith(receiveShare(request));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    openNotificationTarget(
      {
        matchAll: (options) => self.clients.matchAll(options),
        openWindow: (url) => self.clients.openWindow(url),
      },
      event.notification.data,
      self.location.origin,
    ),
  );
});

/** Guarda os arquivos e manda a página escolher a receita (303: a página abre com GET). */
async function receiveShare(request: Request): Promise<Response> {
  try {
    const form = await request.formData();
    const id = await storeShared(self.caches, form, {
      now: Date.now(),
      newId: () => crypto.randomUUID(),
    });
    return redirect(`${SHARE_PATH}?${new URLSearchParams({ id }).toString()}`);
  } catch {
    return redirect(SHARE_UNAVAILABLE);
  }
}

function redirect(path: string): Response {
  return Response.redirect(new URL(path, self.location.origin).href, 303);
}
