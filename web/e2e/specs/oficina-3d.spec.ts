// Cena 3D da abertura (ADR 027 e 030): a imagem estática chega com a página e fica no lugar sem
// WebGL acelerado, com movimento reduzido ou se a cena falhar; com GPU, a cena chega depois do
// `load`, sem CDN, sem violar a CSP, sem entrar no Tab e com a entrada parando em até 5 s.
import AxeBuilder from "@axe-core/playwright";
import type { Page, Request } from "@playwright/test";

import { E2E } from "../support/env";
import { expect, test } from "../support/test";

const HERO = "[data-oficina-3d]";
const CHUNK_OR_MODEL = /\/assets\/oficina-3d[^/]*\.js$|\.glb(\?|$)/;
/**
 * Tempo para o chunk, o GLB e o primeiro quadro. O Chromium do CI desenha em software
 * (SwiftShader): a montagem levou de 5 a 22 s nas medições locais, conforme a carga da máquina.
 */
const SCENE_READY_MS = 45_000;
/** WCAG 2.2.2: movimento automático para sozinho em até 5 s (mais a folga de um quadro). */
const INTRO_LIMIT_MS = 5_500;

/**
 * O Chromium headless não tem GPU: o WebGL dele é em software (SwiftShader), que a página recusa
 * de propósito. Para provar o caminho com GPU, a spec tira a ressalva de desempenho do getContext e
 * troca o nome do renderizador antes de qualquer script da página, como se houvesse placa de vídeo.
 */
async function pretendGpu(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const UNMASKED_RENDERER = 0x9246;
    // Os originais são chamados com o `this` certo pelo Reflect.apply logo abaixo.
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (
      this: HTMLCanvasElement,
      type: string,
      options?: Record<string, unknown>,
    ): unknown {
      const rest = { ...options };
      delete rest["failIfMajorPerformanceCaveat"];
      return Reflect.apply(getContext, this, [type, rest]);
    } as typeof getContext;
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const getParameter = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function (
      this: WebGL2RenderingContext,
      parameter: number,
    ): unknown {
      return parameter === UNMASKED_RENDERER
        ? "E2E GPU"
        : Reflect.apply(getParameter, this, [parameter]);
    };
  });
}

async function withoutWebGl(page: Page): Promise<void> {
  await page.addInitScript(() => {
    // O original é chamado com o `this` certo pelo Reflect.apply logo abaixo.
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (
      this: HTMLCanvasElement,
      type: string,
      options?: unknown,
    ): unknown {
      return type.startsWith("webgl") ? null : Reflect.apply(getContext, this, [type, options]);
    } as typeof getContext;
  });
}

function recordRequests(page: Page): Request[] {
  const requests: Request[] = [];
  page.on("request", (request) => requests.push(request));
  return requests;
}

/** Espera o `load` e dois momentos ociosos: depois disso a página já teria pedido a cena. */
async function waitPastIdle(page: Page): Promise<void> {
  await page.waitForLoadState("load");
  for (let round = 0; round < 2; round += 1) {
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestIdleCallback(() => {
            resolve();
          });
        }),
    );
  }
}

function heroImage(page: Page) {
  return page.getByRole("img", { name: /^Rúbia, goblin da oficina/ });
}

test("com movimento reduzido fica a imagem e nada do 3D é pedido", async ({ page }) => {
  const requests = recordRequests(page);
  await pretendGpu(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await waitPastIdle(page);

  await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "imagem");
  await expect(heroImage(page)).toBeVisible();
  expect(requests.map((r) => r.url()).filter((url) => CHUNK_OR_MODEL.test(url))).toEqual([]);
});

test("sem WebGL fica a imagem e nada do 3D é pedido", async ({ page }) => {
  const requests = recordRequests(page);
  await withoutWebGl(page);
  await page.goto("/");
  await waitPastIdle(page);

  await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "imagem");
  await expect(heroImage(page)).toBeVisible();
  expect(requests.map((r) => r.url()).filter((url) => CHUNK_OR_MODEL.test(url))).toEqual([]);
});

test("WebGL só em software conta como sem WebGL", async ({ page }) => {
  const requests = recordRequests(page);
  await page.goto("/");
  await waitPastIdle(page);

  await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "imagem");
  expect(requests.map((r) => r.url()).filter((url) => CHUNK_OR_MODEL.test(url))).toEqual([]);
});

test.describe("com GPU", () => {
  // Montar a cena em software leva mais que o teto padrão de 20 s da suíte.
  test.slow();
  test.beforeEach(async ({ page }) => {
    await pretendGpu(page);
  });

  test("a cena chega depois do load, só da própria origem, e a entrada para em até 5 s", async ({
    page,
  }) => {
    const requests = recordRequests(page);
    const cspErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error" && /Content Security Policy/i.test(message.text())) {
        cspErrors.push(message.text());
      }
    });
    await page.goto("/");

    const hero = page.locator(HERO);
    await expect(hero).toHaveAttribute("data-oficina-3d", "pronto", { timeout: SCENE_READY_MS });
    await expect(hero.locator("canvas")).toBeVisible();
    await expect(hero.locator("[data-movimento]")).toHaveAttribute("data-movimento", "parado", {
      timeout: INTRO_LIMIT_MS,
    });

    const origin = new URL(E2E.baseUrl).origin;
    const foreign = requests
      .map((r) => r.url())
      .filter((url) => !url.startsWith("blob:") && !url.startsWith("data:"))
      .filter((url) => new URL(url).origin !== origin);
    expect(foreign, "nenhum pedido a CDN ou outro domínio").toEqual([]);
    expect(cspErrors, "nenhuma violação da CSP com a cena montada").toEqual([]);

    const timing = await page.evaluate(() => {
      const navigation = performance.getEntriesByType("navigation")[0] as
        PerformanceNavigationTiming | undefined;
      return {
        loadEnd: navigation?.loadEventEnd ?? Number.POSITIVE_INFINITY,
        chunkStart: performance
          .getEntriesByType("resource")
          .filter((entry) => /\/assets\/oficina-3d[^/]*\.js$/.test(entry.name))
          .map((entry) => entry.startTime),
      };
    });
    expect(timing.chunkStart).toHaveLength(1);
    expect(timing.chunkStart[0] ?? 0).toBeGreaterThanOrEqual(timing.loadEnd);
  });

  test("com a cena montada, o canvas fica fora do leitor de tela e do Tab, e o axe passa", async ({
    page,
  }) => {
    await page.goto("/");
    const hero = page.locator(HERO);
    await expect(hero).toHaveAttribute("data-oficina-3d", "pronto", { timeout: SCENE_READY_MS });

    const canvas = hero.locator("canvas");
    await expect(canvas.locator("xpath=..")).toHaveAttribute("aria-hidden", "true");
    expect(await canvas.evaluate((element) => element.tabIndex)).toBe(-1);
    await expect(heroImage(page)).toBeAttached();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });

  test("se o modelo não vier, fica a imagem", async ({ page }) => {
    await page.route(/\.glb(\?|$)/, (route) => route.fulfill({ status: 404, body: "" }));
    await page.goto("/");

    await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "falhou", {
      timeout: SCENE_READY_MS,
    });
    await expect(heroImage(page)).toBeVisible();
  });
});
