// Captura da vitrine de portfólio (plano 2026-11-22-vitrine-de-portfolio, Tarefa 3). Roda só pela
// config própria (playwright.vitrine.config.ts), contra o build local e a API falsa, e grava em
// docs/img/vitrine/ as telas, os quadros do GIF (em FRAMES_DIR) e o manifesto. Cada captura registra
// as goblins da área: Jade e Fagulha reprovam no teste Python (D3 a), e a área é recortada.
// Nenhuma captura da página de acesso nem de chave criada: a chave canário aparece na tela.
import { execFileSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { Locator, Page } from "@playwright/test";

import {
  CSV_FILES,
  DOWNLOADS_FILES,
  MP4_FILES,
  PDF_FILES,
  PNG_FILES,
  type InputFile,
} from "../fixtures/files";
import { BancadaPage, type RecipeId } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import { HistoricoPage } from "../pages/HistoricoPage";
import { expect, test } from "../support/test";
import {
  cssImageUrls,
  FRAMES_DIR,
  VITRINE_CLOCK,
  VITRINE_DIR,
  goblinsIn,
  knownGoblins,
  renderManifest,
  type VitrineEntry,
} from "./vitrine.ts";

const GOBLIN_ART_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../src/assets/art/goblins",
);
const KNOWN = knownGoblins(GOBLIN_ART_DIR);
const MAX_WIDTH = 1280;
const MAX_HEIGHT = 1600;

interface Recipe {
  readonly recipeId: RecipeId;
  readonly files: readonly InputFile[];
  readonly fillParameters: (bancada: BancadaPage) => Promise<void>;
}

// Os mesmos passos de parâmetros das jornadas (specs/journeys.spec.ts), uma por receita do catálogo.
const RECIPES: readonly Recipe[] = [
  {
    recipeId: "csv-janitor",
    files: CSV_FILES,
    fillParameters: async (bancada) => {
      await bancada.choose("Delimitador dos arquivos de saída", "Ponto e vírgula (;)");
      await bancada.fill("Colunas sem espaços nas pontas", "nome");
    },
  },
  {
    recipeId: "image-forge",
    files: PNG_FILES,
    fillParameters: fillImageForge,
  },
  {
    recipeId: "downloads-detox",
    files: DOWNLOADS_FILES,
    fillParameters: async (bancada) => {
      await bancada.choose("Nomes dos arquivos", "Numerar na ordem de entrada");
      await bancada.fill("Prefixo da numeração", "lote");
    },
  },
  {
    recipeId: "pdf-press",
    files: PDF_FILES,
    fillParameters: async (bancada) => {
      await bancada.choose("O que fazer com os PDFs", "Montar um PDF com as páginas escolhidas");
      await bancada.press("Adicionar página");
      await bancada.choose("PDF da página 1", "contrato.pdf");
      await bancada.press("Adicionar página");
      await bancada.choose("PDF da página 2", "anexo.pdf");
    },
  },
  {
    recipeId: "media-mill",
    files: MP4_FILES,
    fillParameters: async (bancada) => {
      await bancada.pick("FLAC (compressão sem perdas)");
      await bancada.pick("Três, em 10%, 50% e 90% da duração");
    },
  },
];

async function fillImageForge(bancada: BancadaPage): Promise<void> {
  await bancada.choose("Formato de saída", "JPEG");
  await bancada.fill("Largura máxima, em pixels", "800");
}

interface Clip {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

interface Shot {
  readonly path: string;
  readonly goblins: readonly string[];
}

const entries: VitrineEntry[] = [];
const journeyGoblins = new Set<string>();
let frameCount = 0;

/** A área de um elemento na página inteira, limitada ao orçamento (1280 x 1600). */
async function clipOf(locator: Locator): Promise<Clip> {
  const box = await locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      x: rect.left + window.scrollX,
      y: rect.top + window.scrollY,
      width: rect.width,
      height: rect.height,
    };
  });
  return {
    x: Math.max(0, Math.floor(box.x)),
    y: Math.max(0, Math.floor(box.y)),
    width: Math.min(MAX_WIDTH, Math.ceil(box.width)),
    height: Math.min(MAX_HEIGHT, Math.ceil(box.height)),
  };
}

/** A viewport inteira, na rolagem atual. */
async function viewportClip(page: Page): Promise<Clip> {
  const size = page.viewportSize() ?? { width: MAX_WIDTH, height: 800 };
  const y = await page.evaluate(() => Math.floor(window.scrollY));
  return { x: 0, y, width: size.width, height: size.height };
}

/**
 * URLs das imagens visíveis que cruzam a área: `currentSrc` de todo `img` e, de cada elemento e
 * dos seus `::before` e `::after`, o `background-image` e o `content` computados.
 */
async function imageUrlsIn(page: Page, clip: Clip): Promise<string[]> {
  const found = await page.evaluate((area) => {
    const sources: string[] = [];
    const styles: string[] = [];
    const crosses = (element: Element): boolean => {
      const rect = element.getBoundingClientRect();
      const top = rect.top + window.scrollY;
      const left = rect.left + window.scrollX;
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        left < area.x + area.width &&
        left + rect.width > area.x &&
        top < area.y + area.height &&
        top + rect.height > area.y
      );
    };
    const visible = (element: Element): boolean => {
      const style = getComputedStyle(element);
      // Sem filtrar a opacidade: o `animations: "disabled"` da captura termina o fade e mostra
      // uma imagem que ainda estava com opacidade 0. Na dúvida, a goblin conta.
      return style.visibility !== "hidden" && style.display !== "none";
    };
    for (const element of document.querySelectorAll("body *")) {
      if (!crosses(element) || !visible(element)) {
        continue;
      }
      if (element instanceof HTMLImageElement) {
        sources.push(element.currentSrc || element.src);
      }
      for (const pseudo of [null, "::before", "::after"]) {
        const style = getComputedStyle(element, pseudo);
        styles.push(style.backgroundImage, style.content);
      }
    }
    return { sources, styles };
  }, clip);
  return [...found.sources, ...cssImageUrls(found.styles)];
}

/**
 * Grava o PNG da área e devolve as goblins que aparecem nela, lidas antes e depois da captura:
 * uma imagem que termina de carregar no meio também conta.
 */
async function shoot(page: Page, path: string, clip: Clip): Promise<Shot> {
  await page.evaluate(() => document.fonts.ready);
  const before = await imageUrlsIn(page, clip);
  await page.screenshot({ path, clip, fullPage: true, animations: "disabled", caret: "hide" });
  const after = await imageUrlsIn(page, clip);
  return { path, goblins: goblinsIn([...before, ...after], KNOWN) };
}

/** Grava uma imagem da vitrine e a registra no manifesto, com as goblins da área. */
async function capture(
  page: Page,
  target: Omit<VitrineEntry, "goblins">,
  clip: Clip,
): Promise<void> {
  const shot = await shoot(page, join(VITRINE_DIR, target.arquivo), clip);
  entries.push({ ...target, goblins: shot.goblins });
}

/** Um quadro do GIF: a viewport inteira, na rolagem atual, para todos terem o mesmo tamanho. */
async function frame(page: Page): Promise<void> {
  frameCount += 1;
  const name = `quadro-${String(frameCount).padStart(2, "0")}.png`;
  const shot = await shoot(page, join(FRAMES_DIR, name), await viewportClip(page));
  for (const goblin of shot.goblins) {
    journeyGoblins.add(goblin);
  }
}

test.describe.serial("vitrine", () => {
  test.beforeAll(() => {
    for (const dir of [VITRINE_DIR, FRAMES_DIR]) {
      rmSync(dir, { recursive: true, force: true });
      mkdirSync(dir, { recursive: true });
    }
  });

  test.beforeEach(async ({ page }) => {
    await page.clock.setFixedTime(VITRINE_CLOCK);
  });

  for (const recipe of RECIPES) {
    test(`receita-${recipe.recipeId}.png: execução concluída com resultados`, async ({ page }) => {
      const bancada = new BancadaPage(page);
      await bancada.open(recipe.recipeId);
      await bancada.addFiles(recipe.files);
      await recipe.fillParameters(bancada);
      await bancada.run();

      const execucao = new ExecucaoPage(page);
      await execucao.waitForStatus("Concluída");
      await expect(
        page.getByRole("link", { name: new RegExp(`^Baixar ${recipe.recipeId}\\.zip`) }),
      ).toBeVisible();
      if (recipe.recipeId === "media-mill") {
        await expect.poll(() => execucao.imagePreviews()).toHaveLength(2);
      }
      await capture(
        page,
        {
          arquivo: `receita-${recipe.recipeId}.png`,
          tela: `Execução concluída: ${recipe.recipeId}`,
          viewport: "1280x800",
        },
        await clipOf(page.getByRole("main")),
      );
    });
  }

  test("historico.png: as execuções da rodada @fake-api", async ({ page, fakeApi }) => {
    // Cada teste tem o seu contexto e a sua API falsa: a rodada entra semeada, uma por receita.
    RECIPES.forEach((recipe, index) => {
      const total = recipe.files.length;
      fakeApi.seedRun({
        recipe_id: recipe.recipeId,
        status: "succeeded",
        created_at: `2026-10-14T11:5${String(index)}:00Z`,
        progress: { phase: null, processed: total, total },
        results_expire_at: "2026-10-15T12:00:00Z",
      });
    });
    const historico = new HistoricoPage(page);
    await historico.open();
    await expect(historico.rows()).toHaveCount(RECIPES.length);
    await capture(
      page,
      { arquivo: "historico.png", tela: "Histórico", viewport: "1280x800" },
      await clipOf(page.getByRole("main")),
    );
  });

  test("oficina.png: abertura com a imagem estática", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    // D3 (a): o seletor de goblins mostra a Jade e a Fagulha; a captura para logo acima dele.
    const cast = page.getByRole("group", { name: "Escolha a goblin" });
    await expect(cast).toBeVisible();
    const castTop = await cast.evaluate(
      (element) => element.getBoundingClientRect().top + window.scrollY,
    );
    await capture(
      page,
      { arquivo: "oficina.png", tela: "Oficina", viewport: "1280x800" },
      { ...(await viewportClip(page)), height: Math.floor(castTop) - 8 },
    );
  });

  test.describe("celular", () => {
    test.use({ viewport: { width: 400, height: 800 } });

    test("bancada-celular.png: Image Forge com arquivos e parâmetros", async ({ page }) => {
      const bancada = new BancadaPage(page);
      await bancada.open("image-forge");
      await bancada.addFiles(PNG_FILES);
      await fillImageForge(bancada);
      await capture(
        page,
        { arquivo: "bancada-celular.png", tela: "Bancada do Image Forge", viewport: "400x800" },
        await clipOf(page.getByRole("main")),
      );
    });
  });

  test("quadros do GIF: jornada do Image Forge", async ({ page }) => {
    test.setTimeout(60_000);
    const [first, second] = PNG_FILES;
    const bancada = new BancadaPage(page);
    await bancada.open("image-forge");
    await bancada.addFiles(PNG_FILES);
    await frame(page);

    await bancada.moveUp(second.name);
    expect(await bancada.order()).toEqual([second.name, first.name]);
    await frame(page);

    await fillImageForge(bancada);
    await page.getByLabel("Largura máxima, em pixels", { exact: true }).scrollIntoViewIfNeeded();
    await frame(page);

    // Sem quadro da fila nem do andamento: a goblin do carregamento sai da id da execução e pode
    // ser a Fagulha (D3 a; o plano aceita o carregamento fora da vitrine).
    await bancada.run();
    const execucao = new ExecucaoPage(page);
    await execucao.waitForStatus("Concluída");
    await frame(page);

    // O fim da tela: os arquivos da execução, cada um processado.
    await page.getByRole("heading", { name: "Arquivos da execução" }).scrollIntoViewIfNeeded();
    await frame(page);

    expect(frameCount).toBeGreaterThanOrEqual(4);
    entries.push({
      arquivo: "jornada.gif",
      tela: "Jornada do Image Forge",
      viewport: "1280x800",
      goblins: [...journeyGoblins],
    });
  });

  test("vitrine.json: manifesto das capturas", () => {
    const commit = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    writeFileSync(join(VITRINE_DIR, "vitrine.json"), renderManifest(entries, commit));
  });
});
