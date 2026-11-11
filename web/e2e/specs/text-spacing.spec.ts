// Espaçamento de texto (WCAG 1.4.12, AA): com o espaçamento que a pessoa pode impor por folha de
// estilo própria, nenhum texto fica cortado. Aplica os valores do critério em cada tela da matriz e
// procura contêiner que esconde o excesso (`overflow` hidden ou clip) com texto passando da borda.
//
// Limites aceitos (revisão do #260):
// - só corte: texto que vaza com `overflow` visible por cima do vizinho não é acusado;
// - corte por `clip-path` ou `contain: paint` sem `overflow` hidden, e o valor de `<input>` e
//   `<textarea>` (sem `innerText`), passam; `overflow: auto` fica de fora porque ainda rola;
// - não roda no build com aviso global (`NOTICE_SPECS`), então a faixa do aviso não é medida.
import type { Page } from "@playwright/test";

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
interface Clip {
  readonly description: string;
}

for (const screen of SCREENS) {
  test(`${screen.name}: com o espaçamento de texto da WCAG 1.4.12, nenhum texto fica cortado`, async ({
    page,
    fakeApi,
  }) => {
    // Sem transições, o layout lido logo depois da folha nova já é o final.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await screen.open(page, fakeApi);

    // Sem isso, uma folha que deixou de valer passaria em silêncio.
    expect(await applyTextSpacing(page), "word-spacing aplicado ao body").not.toBe("0px");
    const clips = await clippedTexts(page, ROUNDING_PX);
    expect(clips.map((clip) => clip.description)).toEqual([]);
  });
}

/**
 * Aplica a folha por CSSOM (`adoptedStyleSheets`): a CSP de produção barra `<style>` injetado, e a
 * folha construída entra depois das do documento, como a do usuário. Devolve o `word-spacing`
 * calculado do body, prova de que a folha vale. Só cobre o `document`: shadow DOM e iframe teriam de
 * adotar a folha cada um (o app não tem nenhum dos dois).
 */
async function applyTextSpacing(page: Page): Promise<string> {
  return page.evaluate(async (css) => {
    const sheet = new CSSStyleSheet();
    sheet.replaceSync(css);
    document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    // Dois quadros: o primeiro aplica o estilo, o segundo deixa ResizeObserver e afins reagirem.
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          resolve();
        });
      });
    });
    return getComputedStyle(document.body).wordSpacing;
  }, WCAG_TEXT_SPACING);
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
