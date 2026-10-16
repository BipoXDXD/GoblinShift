// Marca (decisão do usuário em 2026-10-15): o lockup do cabeçalho, emblema decorativo mais o nome
// em texto, cabe inteiro em 360 px, e os ícones fixos de public/ saem com o tipo certo, nunca como
// o index.html da SPA.
import type { Locator, Page } from "@playwright/test";

import { expect, test, THEMES, useTheme } from "../support/test";

/** Celular estreito: o lockup inteiro tem de caber aqui, ou o nome iria para sr-only (o nome acessível é o próprio texto). */
const NARROW = { width: 360, height: 740 };
const EMBLEM_HEIGHT_PX = 48;
/** Emblema e texto no mesmo eixo: o centro de um a no máximo 2 px do centro do outro. */
const MAX_CENTER_OFFSET_PX = 2;

function brandLink(page: Page): Locator {
  return page.getByRole("banner").getByRole("link", { name: "GoblinShift", exact: true });
}

async function box(
  locator: Locator,
): Promise<{ x: number; y: number; width: number; height: number }> {
  const measured = await locator.boundingBox();
  expect(measured).not.toBeNull();
  return measured ?? { x: 0, y: 0, width: 0, height: 0 };
}

for (const theme of THEMES) {
  test(`lockup no tema ${theme}: emblema carregado, alinhado ao nome e sem rolagem lateral em 360 px`, async ({
    page,
  }) => {
    await useTheme(page, theme);
    await page.setViewportSize(NARROW);
    await page.goto("/");

    const link = brandLink(page);
    const emblem = link.locator("img");
    const name = link.getByText("GoblinShift", { exact: true });
    await expect(name).toBeVisible();
    await expect(emblem).toBeVisible();
    // Carregou de verdade (CSP, caminho do build, AVIF ou WebP), não só reservou a caixa.
    await expect
      .poll(() => emblem.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0))
      .toBe(true);

    const emblemBox = await box(emblem);
    const nameBox = await box(name);
    expect(emblemBox.height).toBe(EMBLEM_HEIGHT_PX);
    expect(
      Math.abs(emblemBox.y + emblemBox.height / 2 - (nameBox.y + nameBox.height / 2)),
    ).toBeLessThanOrEqual(MAX_CENTER_OFFSET_PX);
    expect(nameBox.x).toBeGreaterThan(emblemBox.x + emblemBox.width);
    expect(nameBox.x + nameBox.width).toBeLessThanOrEqual(NARROW.width);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });
}

test("os ícones do index.html e do manifest chegam com o tipo certo", async ({ page }) => {
  await page.goto("/");

  const linked = await page
    .locator('link[rel="icon"], link[rel="apple-touch-icon"], link[rel="manifest"]')
    .evaluateAll((links) => links.map((link) => link.getAttribute("href") ?? ""));
  const expected: Readonly<Record<string, string>> = {
    "/favicon.ico": "image/x-icon",
    "/favicon.svg": "image/svg+xml",
    "/apple-touch-icon.png": "image/png",
    "/manifest.webmanifest": "application/manifest+json",
    "/icon-192.png": "image/png",
    "/icon-512.png": "image/png",
    "/og-image.jpg": "image/jpeg",
  };
  expect(linked.toSorted()).toEqual(
    ["/apple-touch-icon.png", "/favicon.ico", "/favicon.svg", "/manifest.webmanifest"].toSorted(),
  );
  for (const [path, type] of Object.entries(expected)) {
    const response = await page.request.get(path);
    expect(response.status(), path).toBe(200);
    expect(response.headers()["content-type"], path).toBe(type);
  }
});

test("ícone que não existe é 404, não o index.html", async ({ page }) => {
  // O iOS pede este por conta própria, mesmo com o <link rel="apple-touch-icon">.
  const response = await page.request.get("/apple-touch-icon-precomposed.png");

  expect(response.status()).toBe(404);
  expect(await response.text()).not.toContain('<div id="root">');
});
