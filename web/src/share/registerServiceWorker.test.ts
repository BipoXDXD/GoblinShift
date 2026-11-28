import { describe, expect, it } from "vitest";

import { registerServiceWorker, type ServiceWorkerHost } from "./registerServiceWorker";

function hostWith(register: (url: string, options?: RegistrationOptions) => Promise<unknown>): {
  readonly host: ServiceWorkerHost;
  readonly calls: [string, RegistrationOptions | undefined][];
} {
  const calls: [string, RegistrationOptions | undefined][] = [];
  return {
    calls,
    host: {
      serviceWorker: {
        register: (url, options) => {
          calls.push([url, options]);
          return register(url, options);
        },
      },
    },
  };
}

describe("registro do service worker", () => {
  it("registra /sw.js na raiz em produção", () => {
    const { host, calls } = hostWith(() => Promise.resolve());

    registerServiceWorker(host, true);

    expect(calls).toEqual([["/sw.js", { scope: "/" }]]);
  });

  it("não registra fora de produção", () => {
    const { host, calls } = hostWith(() => Promise.resolve());

    registerServiceWorker(host, false);

    expect(calls).toEqual([]);
  });

  it("navegador sem service worker segue sem erro", () => {
    expect(() => {
      registerServiceWorker({}, true);
    }).not.toThrow();
  });

  it("falha do registro não escreve no console nem rejeita sem tratamento", async () => {
    const { host } = hostWith(() => Promise.reject(new Error("bloqueado")));

    registerServiceWorker(host, true);
    // Uma rejeição sem tratamento derruba o Vitest; o console guard do setup reprova console.*.
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
});
