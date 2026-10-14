// Movimento reduzido (P4, P9; WCAG 2.3.3): com `prefers-reduced-motion: reduce`, nenhuma transição
// nem animação dura mais que um instante, nada fica tocando, e a cena 3D da abertura (ADR 027) nem
// é pedida. Sem a preferência, as transições existem: prova de que a sonda enxerga movimento.
import type { Page } from "@playwright/test";

import { SCREENS } from "../support/screens";
import { expect, test } from "../support/test";

// O base.css reduz tudo a 0,01 ms em vez de zero, para os eventos de fim continuarem disparando.
const REDUCED_MAX_MS = 0.01;
const THREE_D_REQUEST = /oficina-3d|\.glb(\?|$)/;

interface Motion {
  /** Elementos (e pseudo-elementos) com transição ou animação acima do teto. */
  readonly moving: string[];
  /**
   * Animações da página (CSS e Web Animations) tocando com duração acima do teto. Uma transição de
   * 0,01 ms que acabou de começar também aparece como `running`, mas termina no quadro seguinte.
   */
  readonly running: string[];
  /** Maior duração de transição declarada na página, em ms. */
  readonly longestTransitionMs: number;
}

for (const screen of SCREENS) {
  test(`${screen.name}: com movimento reduzido, nada se move nem fica tocando`, async ({
    page,
    fakeApi,
  }) => {
    const threeDRequests: string[] = [];
    page.on("request", (request) => {
      if (THREE_D_REQUEST.test(request.url())) {
        threeDRequests.push(request.url());
      }
    });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await screen.open(page, fakeApi);

    const motion = await readMotion(page, REDUCED_MAX_MS);
    expect(motion.moving).toEqual([]);
    expect(motion.running).toEqual([]);
    expect(threeDRequests, "chunk ou modelo 3D pedido com movimento reduzido").toEqual([]);
  });
}

test("sem a preferência, as transições existem (a sonda não é vazia)", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

  const motion = await readMotion(page, REDUCED_MAX_MS);
  expect(motion.longestTransitionMs).toBeGreaterThan(REDUCED_MAX_MS);
});

async function readMotion(page: Page, maxMs: number): Promise<Motion> {
  return page.evaluate((limitMs) => {
    const toMs = (value: string): number[] =>
      value.split(",").map((part) => {
        const trimmed = part.trim();
        const amount = Number.parseFloat(trimmed);
        return trimmed.endsWith("ms") ? amount : amount * 1000;
      });
    const moving: string[] = [];
    let longestTransitionMs = 0;
    for (const element of document.querySelectorAll("*")) {
      for (const pseudo of [null, "::before", "::after"]) {
        const style = getComputedStyle(element, pseudo);
        const transitions = toMs(style.transitionDuration);
        longestTransitionMs = Math.max(longestTransitionMs, ...transitions);
        const animated = style.animationName !== "none";
        const animations = animated ? toMs(style.animationDuration) : [0];
        if ([...transitions, ...animations].some((ms) => ms > limitMs)) {
          moving.push(
            `${element.tagName.toLowerCase()}${pseudo ?? ""}.${element.getAttribute("class") ?? ""}`,
          );
        }
      }
    }
    const running = document
      .getAnimations()
      .filter((animation) => {
        const timing = animation.effect?.getComputedTiming();
        const totalMs = Number(timing?.activeDuration ?? 0);
        return animation.playState === "running" && totalMs > limitMs;
      })
      .map((animation) => {
        const name = "animationName" in animation ? String(animation.animationName) : "";
        return `${animation.constructor.name} ${name}`.trim();
      });
    return { moving, running, longestTransitionMs };
  }, maxMs);
}
