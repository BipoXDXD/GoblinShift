// As goblins em destaque (pedido do usuário em 2026-10-15, maiores em 2026-11-22): a caixa tem
// 400 px de altura no desktop e 320 px no celular de 360 px, a imagem chega de verdade e nada
// estoura a largura. A goblin não passa disso: o Baixar e a ação do vazio ficam na primeira tela.
import type { Page } from "@playwright/test";

import { BancadaPage } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import { HistoricoPage } from "../pages/HistoricoPage";
import type { FakeApi } from "../support/fakeApi";
import { expectFitsDeviceWidth } from "../support/reflow";
import { expect, test } from "../support/test";

/** O celular mais estreito que a interface atende, com a altura de um aparelho comum. */
const PHONE = { width: 360, height: 740 };
const MIN_HEIGHT_PX = { desktop: 380, mobile: 300 };

interface GoblinScreen {
  readonly name: string;
  readonly open: (page: Page, api: FakeApi) => Promise<void>;
}

const SCREENS: readonly GoblinScreen[] = [
  {
    name: "Bancada vazia",
    open: async (page) => {
      await new BancadaPage(page).open("csv-janitor");
    },
  },
  {
    name: "Histórico vazio",
    open: async (page) => {
      await new HistoricoPage(page).open();
      await expect(page.getByText("Nenhuma execução ainda")).toBeVisible();
    },
  },
  {
    name: "Execução na fila",
    open: async (page, api) => {
      await new ExecucaoPage(page).open(api.seedRun({ status: "queued", queue_ahead: 2 }));
      await expect(page.getByText("Na fila", { exact: true }).first()).toBeVisible();
    },
  },
  {
    name: "Execução concluída",
    open: async (page, api) => {
      await new ExecucaoPage(page).open(api.seedRun({ status: "succeeded" }));
      await expect(page.getByRole("link", { name: /^Baixar csv-janitor\.zip/ })).toBeVisible();
    },
  },
  {
    name: "Execução que falhou",
    open: async (page, api) => {
      await new ExecucaoPage(page).open(
        api.seedRun({
          status: "failed",
          error: { code: "INPUT_INVALID", message: "CSV inválido." },
        }),
      );
      await expect(page.getByText("Falhou", { exact: true }).first()).toBeVisible();
    },
  },
  {
    name: "Página não encontrada",
    open: async (page) => {
      await page.goto("/nao-existe");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    },
  },
];

async function usePhoneWidth(page: Page, project: string): Promise<void> {
  if (project === "mobile") {
    await page.setViewportSize(PHONE);
  }
}

/** Rola até a goblin (ela carrega tarde) e espera a imagem decodificar. */
async function goblinHeight(page: Page): Promise<number> {
  const image = page.locator("main picture img").first();
  await image.scrollIntoViewIfNeeded();
  await expect
    .poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth))
    .toBeGreaterThan(0);
  return (await image.boundingBox())?.height ?? 0;
}

for (const screen of SCREENS) {
  test(`${screen.name}: a goblin aparece em destaque, sem rolagem lateral @fake-api`, async ({
    page,
    fakeApi,
  }, info) => {
    await usePhoneWidth(page, info.project.name);
    await screen.open(page, fakeApi);

    const height = await goblinHeight(page);
    const project = info.project.name === "mobile" ? "mobile" : "desktop";
    expect(height).toBeGreaterThanOrEqual(MIN_HEIGHT_PX[project]);
    await expectFitsDeviceWidth(page);
  });
}

// No desktop vale o notebook comum, de 1280 × 800 (o viewport do projeto).
test("Execução concluída: o Baixar fica na primeira tela, acima da goblin @fake-api", async ({
  page,
  fakeApi,
}, info) => {
  await usePhoneWidth(page, info.project.name);
  await new ExecucaoPage(page).open(fakeApi.seedRun({ status: "succeeded" }));

  const download = page.getByRole("link", { name: /^Baixar csv-janitor\.zip/ });
  await expect(download).toBeInViewport({ ratio: 1 });
});

test("Histórico vazio: a ação do vazio fica na primeira tela @fake-api", async ({ page }, info) => {
  await usePhoneWidth(page, info.project.name);
  await new HistoricoPage(page).open();

  await expect(page.getByRole("link", { name: "Escolher uma receita na Oficina" })).toBeInViewport({
    ratio: 1,
  });
});

// Um `sizes` errado faz a tela 1× baixar o arquivo de 3× (1280 px). Os dois projetos rodam em DPR 1.
test("a goblin baixa a menor largura que cobre a caixa @fake-api", async ({
  page,
  fakeApi,
}, info) => {
  await usePhoneWidth(page, info.project.name);
  await new ExecucaoPage(page).open(
    fakeApi.seedRun({
      status: "failed",
      error: { code: "INPUT_INVALID", message: "CSV inválido." },
    }),
  );

  const image = page.locator("main picture img").first();
  await image.scrollIntoViewIfNeeded();
  const expected = info.project.name === "mobile" ? 256 : 384;
  await expect
    .poll(() => image.evaluate((img: HTMLImageElement) => img.currentSrc))
    .toMatch(new RegExp(`-${String(expected)}w-[^/]*\\.(avif|webp)$`));
});

test("Créditos: as seis goblins do elenco, com nome e imagem carregada", async ({ page }, info) => {
  await usePhoneWidth(page, info.project.name);
  await page.goto("/creditos");

  const cast = page.getByRole("list", { name: "O elenco" });
  await expect(cast.getByRole("figure")).toHaveText([
    "Rúbia",
    "Fagulha",
    "Ferrugem",
    "Jade",
    "Lua",
    "Nyx",
  ]);
  for (const image of await cast.locator("img").all()) {
    await image.scrollIntoViewIfNeeded();
    await expect
      .poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth))
      .toBeGreaterThan(0);
  }
  await expectFitsDeviceWidth(page);
});

// O palco grande é `lg:` (384 × 480); no celular de 360 px ele ocupa a coluna (280 × 350). O
// degrau de 448 × 560 saiu: a 1280 × 800 empurrava as receitas 80 px para longe da dobra.
test("Oficina: o palco aparece em destaque, sem rolagem lateral", async ({ page }, info) => {
  await usePhoneWidth(page, info.project.name);
  await page.goto("/");

  const height = (await page.locator("[data-oficina-3d]").boundingBox())?.height ?? 0;
  if (info.project.name === "mobile") {
    expect(height).toBeGreaterThanOrEqual(340);
  } else {
    expect(height).toBeGreaterThanOrEqual(470);
    expect(height).toBeLessThanOrEqual(490);
  }
  await expectFitsDeviceWidth(page);
});

// No desktop o palco é alto: o título começa na altura do topo dele, sem boiar no meio da coluna.
test("Oficina: o título acompanha o topo do palco", async ({ page }, info) => {
  await usePhoneWidth(page, info.project.name);
  await page.goto("/");

  const title = await page.getByRole("heading", { level: 1 }).boundingBox();
  const stage = await page.locator("[data-oficina-3d]").boundingBox();
  const titleTop = title?.y ?? Number.NaN;
  const stageTop = stage?.y ?? Number.NaN;
  if (info.project.name === "mobile") {
    expect(titleTop + (title?.height ?? 0)).toBeLessThanOrEqual(stageTop);
  } else {
    expect(Math.abs(titleTop - stageTop)).toBeLessThanOrEqual(16);
  }
});

// O `sizes` do palco acompanha a caixa: a tela 1× baixa a largura que cobre o palco, e a 2×, o
// dobro. Com movimento reduzido a cena não vem, e a imagem estática é o que se mede.
const POSTER_WIDTH = {
  desktop: { 1: 384, 2: 960 },
  mobile: { 1: 384, 2: 640 },
} as const;

for (const scale of [1, 2] as const) {
  test.describe(`em tela ${String(scale)}×`, () => {
    test.use({ deviceScaleFactor: scale });

    test("Oficina: a imagem estática baixa a menor largura que cobre o palco", async ({
      page,
    }, info) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await usePhoneWidth(page, info.project.name);
      await page.goto("/");

      const project = info.project.name === "mobile" ? "mobile" : "desktop";
      const expected = POSTER_WIDTH[project][scale];
      const image = page.locator("[data-oficina-3d] img");
      await expect
        .poll(() => image.evaluate((img: HTMLImageElement) => img.currentSrc))
        // Sem o prefixo: a de 384 px é idêntica à do estado, e o Vite emite uma só, `rubia-384w`.
        .toMatch(new RegExp(`rubia-${String(expected)}w-[^/]*\\.(avif|webp)$`));
    });
  });
}
