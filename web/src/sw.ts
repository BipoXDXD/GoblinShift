// Service worker do GoblinShift (ADR 064). Só duas coisas: receber o `POST /compartilhar` do
// `share_target` (manifest.webmanifest) e tratar o clique na notificação de término. Não guarda
// cache de página nem de asset e não intercepta nenhum outro pedido: um deploy novo vale na próxima
// navegação, como sem service worker (`Cache-Control: no-cache` no index.html).

import { purgeExpiredShares } from "./share/inbox";
import { openNotificationTarget } from "./share/notificationClick";
import { isShareRequest, receiveShare } from "./share/receive";

declare const self: ServiceWorkerGlobalScope;

// Versão nova assume na hora: ela não tem estado nem cache para migrar.
self.addEventListener("install", () => {
  void self.skipWaiting();
});

// Ao ativar, apaga os compartilhamentos vencidos que ninguém abriu (prazo de 1 h).
self.addEventListener("activate", (event) => {
  event.waitUntil(
    Promise.all([
      self.clients.claim(),
      purgeExpiredShares(self.caches, Date.now()).catch(() => undefined),
    ]),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (!isShareRequest(request, self.location.origin)) {
    return;
  }
  event.respondWith(
    receiveShare(request, {
      caches: self.caches,
      now: Date.now(),
      newId: () => crypto.randomUUID(),
      origin: self.location.origin,
    }).then((location) => Response.redirect(location, 303)),
  );
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
