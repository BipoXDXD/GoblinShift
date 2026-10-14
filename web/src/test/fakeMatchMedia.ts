import { vi } from "vitest";

export interface FakeColorScheme {
  /** Troca a preferência do sistema e avisa quem escuta `change`, como o navegador. */
  readonly setSystemDark: (dark: boolean) => void;
}

/**
 * `window.matchMedia` falso (o jsdom não tem um): só `(prefers-color-scheme: dark)` responde, com
 * o valor de `systemDark`; as outras consultas não casam.
 */
export function installFakeMatchMedia(systemDark: boolean): FakeColorScheme {
  let dark = systemDark;
  // O app só usa o aviso de mudança e relê `matches`; o evento não carrega nada a mais.
  const listeners = new Set<() => void>();
  const isDarkQuery = (query: string): boolean => query === "(prefers-color-scheme: dark)";
  vi.stubGlobal("matchMedia", (query: string) => ({
    media: query,
    get matches() {
      return isDarkQuery(query) && dark;
    },
    addEventListener: (_type: "change", listener: () => void) => {
      if (isDarkQuery(query)) {
        listeners.add(listener);
      }
    },
    removeEventListener: (_type: "change", listener: () => void) => {
      listeners.delete(listener);
    },
  }));
  return {
    setSystemDark: (next) => {
      dark = next;
      for (const listener of listeners) {
        listener();
      }
    },
  };
}
