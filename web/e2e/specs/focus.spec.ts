// Foco visível e ordem do Tab (P9; WCAG 2.4.3, 2.4.7, 2.4.11 e 2.4.13 como referência do anel).
// Percorre cada tela só com Tab e, em cada parada, mede o anel desenhado de verdade.
import type { Page } from "@playwright/test";
import { parse, toGamut, wcagContrast } from "culori";

import { CSV_FILES } from "../fixtures/files";
import { BancadaPage } from "../pages/BancadaPage";
import { SCREENS } from "../support/screens";
import { expect, test, THEMES, useTheme } from "../support/test";

const MIN_RING_PX = 2;
const MIN_RING_CONTRAST = 3;
// Teto de paradas por tela, só para pegar armadilha de foco: a maior é o Histórico com uma página
// cheia (25 linhas com o link da execução e o "Reusar configuração", mais filtros e moldura).
const MAX_TAB_STOPS = 100;

/** O que o navegador desenhou para o elemento focado, lido no próprio navegador. */
interface FocusStop {
  readonly name: string;
  readonly ring: { style: string; widthPx: number; color: string } | null;
  readonly background: string;
  readonly inViewport: boolean;
  readonly obscured: boolean;
}

// Mapeia para sRGB pelo algoritmo do CSS Color 4 (via OKLCH), como o navegador pinta.
const toRgb = toGamut("rgb", "oklch");

function contrast(foreground: string, background: string): number {
  const fg = parse(foreground);
  const bg = parse(background);
  if (fg === undefined || bg === undefined) {
    throw new Error(`cor ilegível: ${foreground} sobre ${background}`);
  }
  return wcagContrast(toRgb(fg), toRgb(bg));
}

for (const theme of THEMES) {
  for (const screen of SCREENS) {
    test(`${screen.name}, tema ${theme}: todo foco por Tab tem anel de 2 px e 3:1, à vista @keyboard`, async ({
      page,
      fakeApi,
    }) => {
      await useTheme(page, theme);
      await screen.open(page, fakeApi);

      const stops = await tabThrough(page);
      expect(stops.length, "a tela tem algo focável por Tab").toBeGreaterThan(0);
      const problems = stops.flatMap((stop) => ringProblems(stop));
      expect(problems).toEqual([]);
    });
  }
}

test("Oficina: o Tab segue a leitura, do pular para o conteúdo aos cartões @keyboard", async ({
  page,
}) => {
  await page.goto("/");
  // A tela é um chunk lazy: o Tab só começa com ela pintada, senão percorre a moldura vazia.
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const names = (await tabThrough(page)).map((stop) => stop.name);
  expect(names).toEqual([
    "Pular para o conteúdo",
    "GoblinShift",
    "Histórico",
    "Acesso",
    "Tema Sistema",
    "Abrir a Bancada da CSV Janitor",
    "Abrir a Bancada da Image Forge",
    "Abrir a Bancada da Downloads Detox",
    "Abrir a Bancada da PDF Press",
    // O rodapé vem depois de todo o conteúdo.
    "Termos de Uso",
    "Aviso de Privacidade",
    "Créditos",
  ]);
});

test("Bancada: arquivos → lista → parâmetros → Executar, e o foco segue o item movido @keyboard", async ({
  page,
}) => {
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await bancada.addFiles(CSV_FILES);
  const [first, second] = CSV_FILES;

  const names = (await tabThrough(page)).map((stop) => stop.name);
  const order = [
    "Pular para o conteúdo",
    "Oficina",
    "Escolher arquivos",
    // Em cada item, a leitura: nome e ações do arquivo, depois os botões de ordem.
    `Remover ${first.name}`,
    `Mover para cima ${first.name}`,
    `Mover para baixo ${first.name}`,
    `Remover ${second.name}`,
    `Mover para cima ${second.name}`,
    `Mover para baixo ${second.name}`,
    "Delimitador dos arquivos de entrada",
    "Executar",
  ];
  const positions = order.map((name) => names.indexOf(name));
  expect(positions, `paradas: ${names.join(" | ")}`).not.toContain(-1);
  expect(positions).toEqual([...positions].sort((a, b) => a - b));
  // Executar fecha o conteúdo; depois dele, só os links do rodapé.
  expect(names.slice(-4), "Executar é a última parada do conteúdo").toEqual([
    "Executar",
    "Termos de Uso",
    "Aviso de Privacidade",
    "Créditos",
  ]);

  // Mover por teclado: o foco fica no botão do item movido, agora na posição 1.
  await page.getByRole("button", { name: `Mover para cima ${second.name}` }).focus();
  await page.keyboard.press("Enter");
  expect(await bancada.order()).toEqual([second.name, first.name]);
  await expect(page.getByRole("button", { name: `Mover para cima ${second.name}` })).toBeFocused();
});

/**
 * Todas as paradas de Tab da página, do primeiro focável ao último, medidas no navegador.
 *
 * A troca de rota põe o foco no h1 (FocusHeading), no meio da página. Por isso o Tab primeiro
 * avança até sair da página (o foco volta ao body) e só então percorre a volta inteira, que
 * começa no primeiro focável e termina quando o foco sai da página de novo.
 */
async function tabThrough(page: Page): Promise<FocusStop[]> {
  await tabUntilFocusLeavesPage(page, () => Promise.resolve());
  const stops: FocusStop[] = [];
  await tabUntilFocusLeavesPage(page, async () => {
    stops.push(await readStop(page));
  });
  return stops;
}

async function tabUntilFocusLeavesPage(page: Page, onStop: () => Promise<void>): Promise<void> {
  for (let index = 0; index < MAX_TAB_STOPS; index += 1) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(() => document.activeElement === document.body)) {
      return;
    }
    await onStop();
  }
  throw new Error(`mais de ${String(MAX_TAB_STOPS)} paradas de Tab: armadilha de foco?`);
}

async function readStop(page: Page): Promise<FocusStop> {
  const stop = await page.evaluate(readFocusStop);
  if (stop === null) {
    throw new Error("o foco saiu da página no meio da leitura");
  }
  return stop;
}

function ringProblems(stop: FocusStop): string[] {
  if (stop.ring === null) {
    return [`${stop.name}: sem anel de foco`];
  }
  const problems: string[] = [];
  if (stop.ring.style !== "solid") {
    problems.push(`${stop.name}: outline-style ${stop.ring.style}`);
  }
  if (stop.ring.widthPx < MIN_RING_PX) {
    problems.push(`${stop.name}: anel de ${String(stop.ring.widthPx)} px`);
  }
  const ratio = contrast(stop.ring.color, stop.background);
  if (ratio < MIN_RING_CONTRAST) {
    problems.push(`${stop.name}: anel com ${ratio.toFixed(2)}:1 sobre ${stop.background}`);
  }
  if (!stop.inViewport) {
    problems.push(`${stop.name}: fora da viewport`);
  }
  if (stop.obscured) {
    problems.push(`${stop.name}: coberto por outro elemento (2.4.11)`);
  }
  return problems;
}

/**
 * Roda no navegador, depois de qualquer transição do elemento terminar. O anel pode estar no próprio elemento ou num pseudo-elemento: o cartão da
 * Oficina desenha o anel no ::after do link, que cobre o cartão (base.css, .card-link).
 */
async function readFocusStop(): Promise<FocusStop | null> {
  const focused = document.activeElement;
  if (!(focused instanceof HTMLElement) || focused === document.body) {
    return null;
  }
  // Controle visualmente oculto (sr-only) não mostra o próprio anel: quem a pessoa vê é o label
  // que o contém, como o "Escolher arquivos" (base.css, .file-picker). Medir o input aprovaria um
  // anel de 1 px recortado; sem label visível, o anel não existe.
  const focusedRect = focused.getBoundingClientRect();
  const visuallyHidden = focusedRect.width <= 1 || focusedRect.height <= 1;
  const element = visuallyHidden ? (focused.closest("label") ?? focused) : focused;
  // O anel entra com transição (a classe `transition` inclui outline-color): mede o estado final.
  await Promise.all(element.getAnimations().map((animation) => animation.finished));
  const ringOf = (pseudo: string | null) => {
    if (element === focused && visuallyHidden) {
      return null;
    }
    const style = getComputedStyle(element, pseudo);
    const widthPx = Number.parseFloat(style.outlineWidth);
    return style.outlineStyle === "none" || widthPx === 0
      ? null
      : { style: style.outlineStyle, widthPx, color: style.outlineColor };
  };
  const ownRing = ringOf(null);
  const ring = ownRing ?? ringOf("::after") ?? ringOf("::before");
  // O anel fica do lado de fora (outline-offset), sobre o fundo de quem contém a caixa dele.
  const ringBox = ownRing === null ? (element.offsetParent ?? element) : element;
  let background = "rgb(255, 255, 255)";
  for (let node = ringBox.parentElement; node !== null; node = node.parentElement) {
    const color = getComputedStyle(node).backgroundColor;
    if (color !== "rgba(0, 0, 0, 0)" && color !== "transparent") {
      background = color;
      break;
    }
  }
  // WCAG 2.4.11 (mínimo AA): o foco não pode ficar inteiramente escondido. Inteiro à vista seria
  // pedir mais que o navegador faz: num textarea alto, o Chromium rola só até o cursor aparecer.
  const rect = element.getBoundingClientRect();
  const visible = {
    top: Math.max(rect.top, 0),
    bottom: Math.min(rect.bottom, window.innerHeight),
    left: Math.max(rect.left, 0),
    right: Math.min(rect.right, window.innerWidth),
  };
  const inViewport = visible.bottom > visible.top && visible.right > visible.left;
  const hit = document.elementFromPoint(
    (visible.left + visible.right) / 2,
    (visible.top + visible.bottom) / 2,
  );
  const obscured = hit !== null && !element.contains(hit) && !hit.contains(element);
  // Aproximação do nome acessível que basta aqui: aria-label, o label do campo ou o texto dos
  // filhos separado por espaço (o resumo "Tema" + "Sistema" vira "Tema Sistema").
  const label = "labels" in focused ? (focused.labels as NodeListOf<HTMLLabelElement>)[0] : null;
  const childText = Array.from(focused.childNodes, (child) => child.textContent ?? "").join(" ");
  const name = (focused.getAttribute("aria-label") ?? label?.textContent ?? childText)
    .replace(/\s+/g, " ")
    .trim();
  return { name, ring, background, inViewport, obscured };
}
