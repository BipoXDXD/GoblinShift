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
  const width = device?.width ?? 0;
  const content = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(content, overflowMessage(content, width, message)).toBeLessThanOrEqual(width);
}

/** Mensagem da falha: a da spec, ou uma padrão com as duas larguras, para quem lê só o título. */
export function overflowMessage(contentPx: number, devicePx: number, message?: string): string {
  return (
    message ??
    `rolagem lateral: o documento tem ${String(contentPx)} px de largura num aparelho de ${String(devicePx)} px`
  );
}
