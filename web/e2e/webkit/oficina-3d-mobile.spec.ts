// Contratos móveis da aplicação no WebKit. Dispatch de Pointer/TouchEvent testa os handlers;
// não aciona o reconhecedor de pan/pinça do iOS nem comprova mudança de visualViewport.scale.
import type { Locator } from "@playwright/test";

import { HERO, prepareGpu, sceneHost, waitSceneStill } from "../support/oficina3d";
import { expect, test } from "../support/test";

interface TouchPointer {
  readonly type: string;
  readonly id: number;
  readonly x: number;
  readonly y: number;
}

/**
 * Ponteiros sintéticos não têm IDs ativos no browser. Só a captura nativa é substituída durante
 * o dispatch; os handlers, a pose, o renderer e o modelo da aplicação continuam reais.
 * Perda de captura real é verificada separadamente com entrada do mouse.
 */
async function touchPointers(host: Locator, events: readonly TouchPointer[]): Promise<void> {
  await host.evaluate((element, pointers) => {
    // eslint-disable-next-line @typescript-eslint/unbound-method
    const capture = element.setPointerCapture;
    element.setPointerCapture = (): void => undefined;
    try {
      for (const event of pointers) {
        element.dispatchEvent(
          new PointerEvent(event.type, {
            pointerId: event.id,
            pointerType: "touch",
            clientX: event.x,
            clientY: event.y,
            bubbles: true,
            cancelable: true,
          }),
        );
      }
    } finally {
      element.setPointerCapture = capture;
    }
  }, events);
}

test.beforeEach(async ({ page, browserName }, testInfo) => {
  await prepareGpu(page, browserName, testInfo);
  await page.goto("/");
  await waitSceneStill(page);
  await page.getByRole("button", { name: "Pose inicial" }).tap();
});

test("palco permite pan-y e pinch-zoom sem cancelar touchmove nem limitar o viewport", async ({
  page,
}) => {
  const host = sceneHost(page);
  await expect(host).toHaveCSS("touch-action", "pan-y pinch-zoom");
  const viewport = await page.locator('meta[name="viewport"]').getAttribute("content");
  expect(viewport).not.toBeNull();
  expect(viewport).not.toMatch(/user-scalable\s*=\s*(?:no|0)\b/i);
  // Um maximum-scale pequeno também impediria a ampliação para leitura.
  const maximum = /maximum-scale\s*=\s*([\d.]+)/i.exec(viewport ?? "")?.[1];
  expect(maximum === undefined || Number(maximum) >= 2).toBe(true);
  const result = await host.evaluate((element) => {
    const ancestors: string[] = [];
    for (let current: Element | null = element; current !== null; current = current.parentElement) {
      ancestors.push(getComputedStyle(current).touchAction);
    }
    const prevented = [1, 2, 3].map((fingers) => {
      const touches = Array.from({ length: fingers }, (_, identifier) => ({
        identifier,
        target: element,
        clientX: 100 + identifier * 20,
        clientY: 100,
      }));
      // WebKit expõe TouchEvent, mas não o construtor Touch. Fornece as listas sintéticas sem
      // depender desse construtor indisponível; não pede ao browser para reconhecer um gesto.
      const event = new TouchEvent("touchmove", { bubbles: true, cancelable: true });
      for (const key of ["touches", "targetTouches", "changedTouches"]) {
        Object.defineProperty(event, key, { value: touches });
      }
      element.dispatchEvent(event);
      return event.defaultPrevented;
    });
    return { ancestors, prevented };
  });
  expect(result.prevented).toEqual([false, false, false]);
  // A permissão do filho não sobrepõe um ancestral que bloqueia os gestos.
  for (const action of result.ancestors) {
    expect(
      action === "auto" ||
        action === "manipulation" ||
        (action.includes("pan-y") && action.includes("pinch-zoom")),
    ).toBe(true);
  }
});

test("PageDown no palco rola a página sem inclinar a goblin (não simula swipe)", async ({
  page,
}) => {
  const host = sceneHost(page);
  await host.scrollIntoViewIfNeeded();
  await page.getByRole("application", { name: "Rúbia em 3D" }).focus();
  const before = await page.evaluate(() => window.scrollY);
  await page.keyboard.press("PageDown");
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before);
  await expect(host).toHaveAttribute("data-giro", "-20");
  await expect(host).toHaveAttribute("data-inclinacao", "0");
});

for (const end of ["pointercancel", "lostpointercapture"]) {
  test(`${end} limpa dois toques e o novo arrasto não herda dedos (sintético)`, async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const host = sceneHost(page);
    await touchPointers(host, [
      { type: "pointerdown", id: 11, x: 100, y: 100 },
      { type: "pointerdown", id: 12, x: 160, y: 100 },
      { type: end, id: 11, x: 100, y: 100 },
      { type: end, id: 12, x: 160, y: 100 },
      { type: "pointermove", id: 11, x: 140, y: 140 },
      { type: "pointermove", id: 12, x: 200, y: 140 },
    ]);
    await expect(host).toHaveAttribute("data-giro", "-20");
    await expect(host).toHaveAttribute("data-inclinacao", "0");
    await touchPointers(host, [
      { type: "pointerdown", id: 13, x: 100, y: 100 },
      { type: "pointermove", id: 13, x: 140, y: 120 },
      { type: "pointerup", id: 13, x: 140, y: 120 },
    ]);
    // Um dedo: 40 px giram ~23°; 20 px verticais não inclinam.
    await expect(host).toHaveAttribute("data-giro", "3");
    await expect(host).toHaveAttribute("data-inclinacao", "0");
    expect(errors).toEqual([]);
  });
}

test("perda de captura real encerra o arrasto e permite recomeçar", async ({ page }) => {
  const host = sceneHost(page);
  await host.scrollIntoViewIfNeeded();
  const box = await host.boundingBox();
  if (box === null) throw new Error("palco sem caixa");
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await host.evaluate((element) => {
    element.addEventListener(
      "gotpointercapture",
      (event) => {
        if (event instanceof PointerEvent) element.releasePointerCapture(event.pointerId);
      },
      { once: true },
    );
  });
  await page.mouse.move(x, y);
  await page.mouse.down();
  // A captura só é processada no próximo evento de ponteiro.
  await page.mouse.move(x, y);
  await page.mouse.move(x + 40, y + 20);
  await page.mouse.up();
  await expect(host).toHaveAttribute("data-giro", "-20");
  await expect(host).toHaveAttribute("data-inclinacao", "0");
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 40, y + 20);
  await page.mouse.up();
  await expect(host).toHaveAttribute("data-giro", "3");
  await expect(host).toHaveAttribute("data-inclinacao", "11");
  await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "pronto");
});
