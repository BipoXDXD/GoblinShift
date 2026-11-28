import { onTestFinished, vi } from "vitest";

/** Uma notificação criada pelo construtor ou pelo service worker. */
export interface ShownNotification {
  readonly title: string;
  readonly options: NotificationOptions | undefined;
}

export interface FakeNotifications {
  /** O `Notification.requestPermission` falso, que responde `answer`. */
  readonly requestPermission: ReturnType<typeof vi.fn<() => Promise<NotificationPermission>>>;
  /** Notificações criadas por `new Notification`, na ordem. */
  readonly constructed: readonly ShownNotification[];
}

/**
 * `window.Notification` falso (o jsdom não tem um). `permission` começa em `initial` e passa a
 * `answer` quando `requestPermission` responde, como no navegador. O `unstubGlobals` do
 * vite.config.ts o remove ao fim do teste.
 */
export function installFakeNotification(
  initial: NotificationPermission,
  answer: NotificationPermission = initial,
): FakeNotifications {
  const constructed: ShownNotification[] = [];
  let permission = initial;
  const requestPermission = vi.fn<() => Promise<NotificationPermission>>(() => {
    permission = answer;
    return Promise.resolve(answer);
  });
  class FakeNotification {
    static get permission(): NotificationPermission {
      return permission;
    }
    static readonly requestPermission = requestPermission;
    onclick: (() => void) | null = null;
    readonly close = vi.fn();
    constructor(title: string, options?: NotificationOptions) {
      constructed.push({ title, options });
    }
  }
  vi.stubGlobal("Notification", FakeNotification);
  return { requestPermission, constructed };
}

/**
 * `navigator.serviceWorker` falso: `getRegistration` devolve um registro com `showNotification`, ou
 * nenhum. Sai do `navigator` ao fim do teste.
 */
export function installFakeServiceWorker(registered: boolean): readonly ShownNotification[] {
  const shown: ShownNotification[] = [];
  const registration = {
    showNotification: (title: string, options?: NotificationOptions): Promise<void> => {
      shown.push({ title, options });
      return Promise.resolve();
    },
  };
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { getRegistration: () => Promise.resolve(registered ? registration : undefined) },
  });
  onTestFinished(() => {
    Reflect.deleteProperty(navigator, "serviceWorker");
  });
  return shown;
}

/** Troca a visibilidade da aba e avisa quem escuta `visibilitychange`; volta ao padrão no fim. */
export function setDocumentVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
  document.dispatchEvent(new Event("visibilitychange"));
  onTestFinished(() => {
    // Sem a propriedade própria, vale o getter do protótipo do jsdom, que diz "visible".
    Reflect.deleteProperty(document, "visibilityState");
  });
}
