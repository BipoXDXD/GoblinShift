// WCAG 1.4.10 (Reflow): a página cabe na largura do aparelho, sem rolagem lateral.
import type { Page } from "@playwright/test";

import { expect } from "./test";

/**
 * Afirma que o documento não é mais largo que o aparelho: o viewport do projeto, ou o que a spec
 * pôs com `setViewportSize`.
 *
 * A régua não é o `innerWidth`. Com `isMobile`, o Chromium alarga o layout viewport até o conteúdo
 * que estoura, e o `innerWidth` cresce junto com o `scrollWidth`: `scrollWidth <= innerWidth`
 * passa no celular com a página estourando.
 */
export async function expectFitsDeviceWidth(page: Page, message?: string): Promise<void> {
  const device = page.viewportSize();
  expect(device, "a spec não tem viewport para servir de régua").not.toBeNull();
  const content = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(content, message).toBeLessThanOrEqual(device?.width ?? 0);
}
