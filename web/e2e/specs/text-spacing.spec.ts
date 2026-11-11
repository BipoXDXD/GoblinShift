// Espaçamento de texto (WCAG 1.4.12, AA): com o espaçamento que a pessoa pode impor por folha de
// estilo própria, nenhum texto fica cortado. Aplica os valores do critério em cada tela da matriz e
// procura contêiner que esconde o excesso (`overflow` hidden ou clip) com texto passando da borda.
//
// Limites aceitos (revisão do #260):
// - só corte: texto que vaza com `overflow` visible por cima do vizinho não é acusado;
// - corte por `clip-path` ou `contain: paint` sem `overflow` hidden, e o valor de `<input>` e
//   `<textarea>` (sem `innerText`), passam; `overflow: auto` fica de fora porque ainda rola;
// - não roda no build com aviso global (`NOTICE_SPECS`), então a faixa do aviso não é medida.
import type { JSHandle, Page } from "@playwright/test";

import { SCREENS } from "../support/screens";
import { expect, test } from "../support/test";

// Os quatro valores mínimos da 1.4.12, como no bookmarklet de teste do W3C.
const WCAG_TEXT_SPACING = `
  * {
    line-height: 1.5 !important;
    letter-spacing: 0.12em !important;
    word-spacing: 0.16em !important;
  }
  p { margin-bottom: 2em !important; }
`;
/** Folga para o arredondamento de subpixel entre `scrollHeight` e `clientHeight`. */
const ROUNDING_PX = 1;
/** Teto para as transições assentarem, bem abaixo dos 20 s do teste: estourar é erro descritivo. */
const TRANSITION_BUDGET_MS = 5000;
/** Quantos rótulos distintos o erro de teto lista; o resto vira contagem. */
const MAX_LISTED_ANIMATIONS = 8;
interface Clip {
  readonly description: string;
}

for (const screen of SCREENS) {
  test(`${screen.name}: com o espaçamento de texto da WCAG 1.4.12, nenhum texto fica cortado`, async ({
    page,
    fakeApi,
  }) => {
    // O movimento reduzido encurta as transições para 0,01 ms, não as remove: `applyTextSpacing`
    // espera as que sobrarem.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await screen.open(page, fakeApi);

    // Sem isso, uma folha que deixou de valer passaria em silêncio.
    expect(await applyTextSpacing(page), "word-spacing aplicado ao body").not.toBe("0px");
    const clips = await clippedTexts(page, ROUNDING_PX);
    expect(clips.map((clip) => clip.description)).toEqual([]);
  });
}

test("a folha só é dada como aplicada depois das transições em curso", async ({ page }) => {
  // Sem `reducedMotion`: aqui a transição é longa de verdade (400 ms), muito além de dois quadros.
  await page.goto("/termos");
  await expect(
    page.getByRole("heading", { level: 1, name: "Termos de Uso do GoblinShift" }),
  ).toBeVisible();
  await page.evaluate(() => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync("* { transition-duration: 400ms; }");
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  });

  await applyTextSpacing(page);

  const stillRunning = await withRunningAnimations(page, (running) =>
    running.evaluate(describeAnimations),
  );
  expect(stillRunning, "transições visíveis ainda em curso depois de aplicar a folha").toEqual([]);
  // 16px de fonte do body × 0,16em, calculado à mão.
  expect(await page.evaluate(() => getComputedStyle(document.body).wordSpacing)).toBe("2.56px");
});

/**
 * Aplica a folha por CSSOM (`adoptedStyleSheets`): a CSP de produção barra `<style>` injetado, e a
 * folha construída entra depois das do documento, como a do usuário. Só devolve com as transições
 * assentadas e devolve o `word-spacing` calculado do body, prova de que a folha vale. Só cobre o
 * `document`: shadow DOM e iframe teriam de adotar a folha cada um (o app não tem nenhum dos dois).
 *
 * Por que esperar as transições, e não contar quadros: o `transition-duration: 0.01ms` do movimento
 * reduzido (base.css) não é "sem transição". Com o `transition-property: all` inicial, toda
 * propriedade que a folha muda ganha uma transição, e até ela terminar o `getComputedStyle` devolve
 * o valor de partida ("0px"). Medido: com um quadro a leitura é "0px" sempre, com dois já é o valor
 * final. Dois quadros fixos são, então, o mínimo exato, sem folga para um quadro atrasado na CI.
 */
async function applyTextSpacing(page: Page): Promise<string> {
  await page.evaluate((css) => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
  }, WCAG_TEXT_SPACING);
  const deadline = Date.now() + TRANSITION_BUDGET_MS;
  // Um quadro entre as rodadas deixa ResizeObserver e afins reagirem, e o que eles mudarem entra na
  // rodada seguinte; só sai quando um quadro inteiro passa sem transição em curso.
  for (;;) {
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() => {
            resolve();
          });
        }),
    );
    const { pending, remainingMs } = await withRunningAnimations(page, async (running) => {
      const labels = await running.evaluate(describeAnimations);
      const left = deadline - Date.now();
      if (labels.length > 0 && left > 0) {
        // `finished` de uma animação infinita ou pausada nunca resolve: a corrida contra o tempo
        // devolve a vez ao laço, que então estoura o teto com a lista do que sobrou.
        await running.evaluate(
          (animations, timeoutMs) =>
            Promise.race([
              Promise.allSettled(animations.map((animation) => animation.finished)),
              new Promise((resolve) => setTimeout(resolve, timeoutMs)),
            ]),
          left,
        );
      }
      return { pending: labels, remainingMs: left };
    });
    if (pending.length === 0) {
      break;
    }
    if (remainingMs <= 0) {
      const labels = [...new Set(pending)];
      const listed = labels.slice(0, MAX_LISTED_ANIMATIONS).join(", ");
      const rest = labels.length - MAX_LISTED_ANIMATIONS;
      throw new Error(
        `As transições não assentaram em ${String(TRANSITION_BUDGET_MS)} ms depois de aplicar a ` +
          `folha de espaçamento; ${String(pending.length)} em curso: ${listed}` +
          (rest > 0 ? ` e mais ${String(rest)} tipos` : ""),
      );
    }
  }
  return page.evaluate(() => getComputedStyle(document.body).wordSpacing);
}

/**
 * Entrega ao `use` o handle das animações visíveis em curso e o libera sempre, mesmo se o `use`
 * lançar (um `expect` que falha, um `evaluate` que cai com a página fechada).
 */
async function withRunningAnimations<T>(
  page: Page,
  use: (running: JSHandle<Animation[]>) => Promise<T>,
): Promise<T> {
  const running = await page.evaluateHandle(visibleRunningAnimations);
  try {
    return await use(running);
  } finally {
    await running.dispose();
  }
}

/**
 * Transições e animações em curso e desenhadas. Roda na página (`evaluateHandle`), então não pode
 * usar nada do escopo do módulo. Só o que está desenhado: o conteúdo de um `<details>` fechado
 * ganha a transição, mas ela nunca anda (não há quadro para ele), e a medição de corte também o
 * ignora. `getAnimations()` já resolve o estilo pendente, então enxerga as recém-criadas.
 */
function visibleRunningAnimations(): Animation[] {
  return document.getAnimations().filter((animation) => {
    const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
    return animation.playState !== "finished" && target?.checkVisibility() === true;
  });
}

/** Rótulo legível de cada animação, para a mensagem de erro (também roda na página). */
function describeAnimations(animations: Animation[]): string[] {
  return animations.map((animation) => {
    const target = animation.effect instanceof KeyframeEffect ? animation.effect.target : null;
    const name = animation instanceof CSSTransition ? animation.transitionProperty : "animation";
    return `${target?.tagName.toLowerCase() ?? "?"}:${name}`;
  });
}

/** Contêineres visíveis, com texto, que escondem o que passa da borda num eixo e transbordam nele. */
async function clippedTexts(page: Page, roundingPx: number): Promise<Clip[]> {
  return page.evaluate((tolerance) => {
    const hides = (overflow: string): boolean => overflow === "hidden" || overflow === "clip";
    // O `sr-only` corta de propósito: o texto é só para o leitor de tela.
    const visuallyHidden = (element: Element): boolean => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.clipPath === "inset(50%)" || (box.width <= 1 && box.height <= 1);
    };
    const clipped: Clip[] = [];
    for (const element of document.querySelectorAll("body *")) {
      // SVG não tem `innerText`; o texto que ele desenha não depende do CSS de espaçamento.
      if (!(element instanceof HTMLElement)) {
        continue;
      }
      const style = getComputedStyle(element);
      const hidesX = hides(style.overflowX);
      const hidesY = hides(style.overflowY);
      if (!hidesX && !hidesY) {
        continue;
      }
      const text = element.innerText.trim();
      if (text === "" || !element.checkVisibility() || visuallyHidden(element)) {
        continue;
      }
      const overX = hidesX && element.scrollWidth > element.clientWidth + tolerance;
      const overY = hidesY && element.scrollHeight > element.clientHeight + tolerance;
      if (overX || overY) {
        const name = `${element.tagName.toLowerCase()}.${element.getAttribute("class") ?? ""}`;
        const size = `${String(element.scrollWidth)}×${String(element.scrollHeight)} em ${String(element.clientWidth)}×${String(element.clientHeight)}`;
        clipped.push({
          description: `${name} (${size}): ${text.slice(0, 60)}`,
        });
      }
    }
    return clipped;
  }, roundingPx);
}
