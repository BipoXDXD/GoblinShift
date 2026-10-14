// CLS < 0,05 (P9): a página não pula enquanto carrega fontes, catálogo, execução e resultados. O
// valor é a soma de todo `layout-shift` sem interação recente, mais estrita que a janela de sessão
// do Core Web Vitals.
import type { Page } from "@playwright/test";

import { ExecucaoPage } from "../pages/ExecucaoPage";
import { BancadaPage } from "../pages/BancadaPage";
import { expect, test } from "../support/test";

const MAX_CLS = 0.05;

/** Registra cada deslocamento desde o início do carregamento, antes de qualquer script da página. */
async function recordLayoutShifts(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const shifts: number[] = [];
    Object.defineProperty(window, "__layoutShifts", { value: shifts });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        // LayoutShift não está no lib.dom do TypeScript; os dois campos são da spec do CLS.
        const shift = entry as PerformanceEntry & { value: number; hadRecentInput: boolean };
        if (!shift.hadRecentInput) {
          shifts.push(shift.value);
        }
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
}

/**
 * CLS acumulado com a página assentada: fontes prontas e meio segundo sem rede (networkidle), para
 * um deslocamento tardio, como um aviso que chega depois do conteúdo, entrar na conta. Depois,
 * dois quadros, para o observador entregar o último deslocamento.
 */
async function cumulativeLayoutShift(page: Page): Promise<number> {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForLoadState("networkidle");
  return page.evaluate(
    () =>
      new Promise<number>((resolve) => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            const shifts = (window as unknown as { __layoutShifts: number[] }).__layoutShifts;
            resolve(shifts.reduce((total, value) => total + value, 0));
          });
        });
      }),
  );
}

test.beforeEach(async ({ page }) => {
  await recordLayoutShifts(page);
});

test("Oficina: CLS abaixo de 0,05 até as fontes carregarem", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: /CSV Janitor/ })).toBeVisible();
  expect(await cumulativeLayoutShift(page)).toBeLessThan(MAX_CLS);
});

test("Bancada: CLS abaixo de 0,05 do carregamento do catálogo ao formulário", async ({ page }) => {
  await new BancadaPage(page).open("csv-janitor");
  expect(await cumulativeLayoutShift(page)).toBeLessThan(MAX_CLS);
});

test("Execução: CLS abaixo de 0,05 do carregamento aos resultados", async ({ page, fakeApi }) => {
  const execucao = new ExecucaoPage(page);
  await execucao.open(fakeApi.seedRun({ status: "succeeded" }));
  await expect(page.getByRole("link", { name: /^Baixar csv-janitor\.zip/ })).toBeVisible();
  expect(await cumulativeLayoutShift(page)).toBeLessThan(MAX_CLS);
});
