import type { Page, Route } from "@playwright/test";

/**
 * Segura o primeiro pedido `method` que casar com `url` e devolve a rota para o teste responder
 * quando quiser (o pedido em voo). Os outros métodos seguem para a API falsa.
 */
export function holdRequest(page: Page, url: string, method: string): Promise<Route> {
  return new Promise<Route>((resolve) => {
    void page.route(url, async (route) => {
      if (route.request().method() === method) {
        resolve(route);
      } else {
        await route.fallback();
      }
    });
  });
}
