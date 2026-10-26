// Auxiliares compartilhados das regressões do palco; nenhuma substituição da cena/GLB real.
import type { Locator, Page, Request, TestInfo } from "@playwright/test";

import { expect } from "./test";

declare global {
  interface Window {
    /** Desenha um quadro segurado; só existe depois de {@link holdFrames}. */
    e2eNextFrame?: () => void;
  }
}

export const HERO = "[data-oficina-3d]";
export const CHUNK_OR_MODEL = /\/assets\/oficina-3d[^/]*\.js$|\.glb(\?|$)/;
const MODEL = /\/assets\/oficina-3d-([a-z]+)-[^/]*\.glb$/;
const CAST_GROUP = "Escolha a goblin";
/**
 * Tempo para o chunk, o GLB e o primeiro quadro. O Chromium do CI desenha em software
 * (SwiftShader): a montagem levou de 5 a 22 s nas medições locais, conforme a carga da máquina.
 */
export const SCENE_READY_MS = 45_000;
/** WCAG 2.2.2: movimento automático para sozinho em até 5 s (mais a folga de um quadro). */
export const INTRO_LIMIT_MS = 5_500;

/**
 * O Chromium headless pode oferecer só WebGL em software, que a página recusa de propósito.
 * Para provar o caminho da cena, a spec tira a ressalva de desempenho do getContext e
 * troca o nome do renderizador antes de qualquer script da página, como se houvesse placa de vídeo.
 */
export async function pretendGpu(
  page: Page,
  renderer = "E2E GPU",
): Promise<void> {
  await page.addInitScript((rendererName: string) => {
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
        ? rendererName
        : Reflect.apply(getParameter, this, [parameter]);
    };
  }, renderer);
}

/** Quanto o relógio dos quadros anda a cada {@link nextFrame}: a 30° por segundo, 30° de giro. */
const FRAME_STEP_MS = 1_000;

/**
 * Girando, a cena pede um quadro atrás do outro, e cada um prende a thread principal por segundos
 * no SwiftShader: o clique e os scripts do Playwright esperavam mais que o actionTimeout entre um
 * quadro e outro (runs 38866217252 e 38871601000). Com a cena já parada, a spec passa a segurar os
 * quadros da página: o requestAnimationFrame só enfileira, e cada {@link nextFrame} desenha um com
 * o relógio adiantado de FRAME_STEP_MS. A thread fica livre entre os quadros, como com GPU.
 */
export async function holdFrames(page: Page): Promise<void> {
  await page.evaluate((stepMs: number) => {
    const queued = new Map<number, FrameRequestCallback>();
    let nextId = 1;
    let clock = performance.now();
    window.requestAnimationFrame = (callback: FrameRequestCallback): number => {
      const id = nextId;
      nextId += 1;
      queued.set(id, callback);
      return id;
    };
    window.cancelAnimationFrame = (id: number): void => {
      queued.delete(id);
    };
    window.e2eNextFrame = (): void => {
      clock += stepMs;
      const callbacks = [...queued.values()];
      queued.clear();
      for (const callback of callbacks) {
        callback(clock);
      }
    };
  }, FRAME_STEP_MS);
}

/** Desenha um quadro segurado por {@link holdFrames}. */
export async function nextFrame(page: Page): Promise<void> {
  await page.evaluate(() => {
    if (window.e2eNextFrame === undefined) {
      throw new Error("os quadros não estão segurados: chame holdFrames antes");
    }
    window.e2eNextFrame();
  });
}

export async function withoutWebGl(page: Page): Promise<void> {
  await page.addInitScript(() => {
    // O original é chamado com o `this` certo pelo Reflect.apply logo abaixo.
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (
      this: HTMLCanvasElement,
      type: string,
      options?: unknown,
    ): unknown {
      return type.startsWith("webgl")
        ? null
        : Reflect.apply(getContext, this, [type, options]);
    } as typeof getContext;
  });
}

export function recordRequests(page: Page): Request[] {
  const requests: Request[] = [];
  page.on("request", (request) => requests.push(request));
  return requests;
}

/** Espera o `load` e dois momentos ociosos: depois disso a página já teria pedido a cena. */
export async function waitPastIdle(page: Page): Promise<void> {
  await page.waitForLoadState("load");
  for (let round = 0; round < 2; round += 1) {
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          if (typeof window.requestIdleCallback === "function") {
            window.requestIdleCallback(
              () => {
                resolve();
              },
              { timeout: 2_000 },
            );
          } else {
            // Mesmo teto do agendamento da aplicação, também no Safari sem esta API.
            window.setTimeout(resolve, 2_100);
          }
        }),
    );
  }
}

export function heroImage(page: Page, name = "Rúbia"): Locator {
  return page
    .locator(HERO)
    .getByRole("img", { name: new RegExp(`^${name}, goblin`) });
}

export function castButton(page: Page, name: string): Locator {
  return page
    .getByRole("group", { name: CAST_GROUP })
    .getByRole("button", { name });
}

/** As goblins cujo GLB a página pediu, na ordem dos pedidos. */
export function modelsRequested(requests: readonly Request[]): string[] {
  return requests.flatMap(
    (request) => MODEL.exec(new URL(request.url()).pathname)?.[1] ?? [],
  );
}

/** Espera a cena da goblin ficar pronta e a entrada parar, para mexer sem disputar quadros. */
export async function waitSceneStill(page: Page): Promise<void> {
  await expect(page.locator(HERO)).toHaveAttribute(
    "data-oficina-3d",
    "pronto",
    {
      timeout: SCENE_READY_MS,
    },
  );
  await expect(sceneHost(page)).toHaveAttribute("data-movimento", "parado", {
    timeout: INTRO_LIMIT_MS,
  });
}

/** O elemento que a cena marca com a pose e o movimento (o host do canvas). */
export function sceneHost(page: Page): Locator {
  return page.locator(HERO).locator("[data-movimento]");
}

/** Registra o backend antes de qualquer disfarce; WebKit exige aceleração real. */
export async function prepareGpu(
  page: Page,
  browserName: string,
  testInfo: TestInfo,
): Promise<void> {
  const capability = await page.evaluate((allowSoftware: boolean) => {
    const gl = document
      .createElement("canvas")
      .getContext("webgl2", { failIfMajorPerformanceCaveat: !allowSoftware });
    const info = gl?.getExtension("WEBGL_debug_renderer_info");
    const renderer: unknown =
      gl && info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : null;
    gl?.getExtension("WEBGL_lose_context")?.loseContext();
    return {
      webgl2: gl !== null,
      renderer: typeof renderer === "string" ? renderer : null,
      userAgent: navigator.userAgent,
    };
  }, browserName === "chromium");
  await testInfo.attach("webgl-capability", {
    body: JSON.stringify(capability, null, 2),
    contentType: "application/json",
  });
  if (browserName === "chromium") {
    expect(
      capability.webgl2,
      "Chromium sem WebGL 2; veja webgl-capability",
    ).toBe(true);
    await pretendGpu(page);
    return;
  }
  expect(
    capability.webgl2,
    "WebKit sem WebGL 2: execute em macOS com GPU; veja webgl-capability",
  ).toBe(true);
  expect(
    capability.renderer ?? "",
    "WebKit exige aceleração real, sem disfarçar software",
  ).not.toMatch(/swiftshader|llvmpipe|softpipe|software|basic render/i);
}
