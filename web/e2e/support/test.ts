// `test` e `expect` de todas as specs: a chave entra por rota em cada contexto, e no modo local a
// API falsa responde. Nenhuma spec monta isso à mão.
import { test as base, expect, type Page } from "@playwright/test";

import { injectApiKey } from "./apiKey";
import { E2E } from "./env";
import { installFakeApi, type FakeApi } from "./fakeApi";

interface Fixtures {
  /** API falsa do modo local; a spec que a usa é pulada no modo remoto. */
  fakeApi: FakeApi;
  /** Liga a API (falsa ou real) e a chave em todo contexto, sem a spec pedir. */
  api: FakeApi | null;
}

interface Options {
  /**
   * `api-key`: a chave do processo de teste vai em todo pedido (padrão, e o único modo contra a
   * VPS). `none`: o navegador começa sem credencial, para as specs que entram pelo login.
   */
  auth: "api-key" | "none";
}

export const test = base.extend<Fixtures & Options>({
  auth: ["api-key", { option: true }],
  api: [
    async ({ context, auth }, use) => {
      const fake =
        E2E.artifactsDir === null
          ? null
          : await installFakeApi(context, E2E.apiKey, E2E.artifactsDir);
      if (auth === "api-key" && E2E.hasAccount) {
        await injectApiKey(context, E2E.apiKey, E2E.baseUrl);
      }
      await use(fake);
      // Pedido que a API falsa não conhece é rota nova na interface sem cobertura aqui.
      expect(fake?.unhandled ?? [], "pedidos sem rota na API falsa").toEqual([]);
    },
    { auto: true },
  ],
  fakeApi: async ({ api }, use) => {
    test.skip(api === null, "estado forçado só existe com a API falsa (modo local)");
    if (api !== null) {
      await use(api);
    }
  },
});

export { expect };

export type Theme = "light" | "dark";
export const THEMES: readonly Theme[] = ["light", "dark"];

/**
 * Abre a página já no tema: a preferência gravada, como depois de escolher no seletor, e o sistema
 * no mesmo tema, para `prefers-color-scheme` não mascarar um `data-theme` errado.
 */
export async function useTheme(page: Page, theme: Theme): Promise<void> {
  await page.emulateMedia({ colorScheme: theme });
  await page.addInitScript((value) => {
    localStorage.setItem("gs-theme", value);
  }, theme);
}
