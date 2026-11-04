// CLS < 0,05 (P9): a página não pula enquanto carrega fontes, catálogo, execução e resultados. O
// valor é a soma de todo `layout-shift` sem interação recente, mais estrita que a janela de sessão
// do Core Web Vitals.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import type { Locator, Page } from "@playwright/test";

import { ExecucaoPage } from "../pages/ExecucaoPage";
import { BancadaPage } from "../pages/BancadaPage";
import { expect, test } from "../support/test";

const MAX_CLS = 0.05;
/** Bem depois do período de bloqueio da fonte (até 100 ms no swap e no optional). */
const LATE_FONT_MS = 1_000;
const MONO_FONT =
  "@fontsource-variable/atkinson-hyperlegible-mono/files/atkinson-hyperlegible-mono-latin-wght-normal.woff2";

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

/** Uma tela aberta sem esperar o `load` e o elemento que diz que ela está pintada. */
interface Screen {
  readonly path: string;
  readonly ready: (page: Page) => Locator;
}

const OFICINA: Screen = {
  path: "/",
  ready: (page) => page.getByRole("link", { name: /CSV Janitor/ }),
};
const BANCADA: Screen = {
  path: "/bancada/csv-janitor",
  ready: (page) => page.getByRole("button", { name: "Executar" }),
};

/**
 * Segura a fonte que casa com `fontGlob` até a tela estar pintada e passar o período de bloqueio do
 * `font-display` (até 100 ms no swap e no optional), e responde com a Atkinson Mono, de métricas bem
 * diferentes de qualquer fallback: se a fonte tardia ainda for aplicada, o texto se move, seja qual
 * for a fonte do sistema.
 */
async function openWithLateFont(page: Page, screen: Screen, fontGlob: string): Promise<void> {
  const lateFont = readFileSync(fileURLToPath(import.meta.resolve(MONO_FONT)));
  let markPainted = (): void => undefined;
  const painted = new Promise<void>((resolve) => {
    markPainted = resolve;
  });
  await page.route(fontGlob, async (route) => {
    const blockPeriodOver = new Promise((resolve) => setTimeout(resolve, LATE_FONT_MS));
    await Promise.all([painted, blockPeriodOver]);
    await route.fulfill({ contentType: "font/woff2", body: lateFont });
  });
  // Sem esperar o `load`: o preload da fonte segurada o atrasaria até a resposta.
  await page.goto(screen.path, { waitUntil: "commit" });
  await expect(screen.ready(page)).toBeVisible();
  markPainted();
}

const TEXT_FONT = "**/assets/atkinson-hyperlegible-next-*.woff2";
const TITLE_FONT = "**/assets/bricolage-grotesque-*.woff2";

// No CI, a Atkinson do texto às vezes chegava depois da primeira pintura: o fallback (DejaVu Sans)
// é mais largo, o aviso quebrava numa linha a mais e o conteúdo subia 24 px na troca (CLS 0,134).
test("Bancada: CLS abaixo de 0,05 com a fonte do texto chegando depois da primeira pintura", async ({
  page,
}) => {
  await openWithLateFont(page, BANCADA, TEXT_FONT);
  expect(await cumulativeLayoutShift(page)).toBeLessThan(MAX_CLS);
});

// A do título segue em swap (ADR 040): os títulos ocupam poucas linhas e a troca quase não mexe
// na altura. A Oficina, com o título de abertura em várias linhas, é o caso mais apertado.
for (const [name, screen] of [
  ["Oficina", OFICINA],
  ["Bancada", BANCADA],
] as const) {
  test(`${name}: CLS abaixo de 0,05 com a fonte do título chegando depois da primeira pintura`, async ({
    page,
  }) => {
    await openWithLateFont(page, screen, TITLE_FONT);
    expect(await cumulativeLayoutShift(page)).toBeLessThan(MAX_CLS);
  });
}

test("Execução: CLS abaixo de 0,05 do carregamento aos resultados", async ({ page, fakeApi }) => {
  const execucao = new ExecucaoPage(page);
  await execucao.open(fakeApi.seedRun({ status: "succeeded" }));
  await expect(page.getByRole("link", { name: /^Baixar csv-janitor\.zip/ })).toBeVisible();
  expect(await cumulativeLayoutShift(page)).toBeLessThan(MAX_CLS);
});
