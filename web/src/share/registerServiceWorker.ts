/** O pedaço do `navigator` que o registro usa; o teste passa um falso. */
export interface ServiceWorkerHost {
  readonly serviceWorker?: {
    register: (url: string, options?: RegistrationOptions) => Promise<unknown>;
  };
}

/**
 * Registra o service worker do compartilhamento e do aviso de término (ADR 064), só no build de
 * produção: no `vite dev` não existe `/sw.js`. Sem suporte ou com falha no registro, o site segue
 * igual, sem o "Compartilhar" do celular; a falha não vai ao console (console guard).
 */
export function registerServiceWorker(
  host: ServiceWorkerHost = navigator,
  production: boolean = import.meta.env.PROD,
): void {
  if (!production || host.serviceWorker === undefined) {
    return;
  }
  host.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined);
}
