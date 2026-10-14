import type { BrowserContext } from "@playwright/test";

/**
 * Põe a chave de API em cada pedido para a API, no processo do Playwright (plano da fatia 7, 4.2).
 * O bundle, o storage e os cookies nunca a veem: o navegador manda o pedido sem credencial e o
 * Playwright acrescenta o cabeçalho no caminho, inclusive nos downloads por navegação.
 *
 * Registrada depois da API falsa, roda antes dela (o Playwright chama a última rota primeiro) e
 * passa o pedido adiante com `fallback`: para a API falsa no modo local e para a rede no remoto.
 */
export async function injectApiKey(context: BrowserContext, apiKey: string): Promise<void> {
  await context.route("**/api/v1/**", async (route) => {
    const headers = { ...route.request().headers(), authorization: `Bearer ${apiKey}` };
    await route.fallback({ headers });
  });
}
