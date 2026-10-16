// As goblins em destaque (pedido do usuário em 2026-10-15): a caixa tem ~320 px de altura no
// desktop e ~240 px no celular de 360 px, a imagem chega de verdade e nada estoura a largura.
import type { Page } from "@playwright/test";

import { BancadaPage } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import { HistoricoPage } from "../pages/HistoricoPage";
import type { FakeApi } from "../support/fakeApi";
import { expect, test } from "../support/test";

/** O celular mais estreito que a interface atende, com a altura de um aparelho comum. */
const PHONE = { width: 360, height: 740 };
const MIN_HEIGHT_PX = { desktop: 280, mobile: 200 };

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
  test(`${screen.name}: a goblin aparece em destaque, sem rolagem lateral`, async ({
    page,
    fakeApi,
  }, info) => {
    await usePhoneWidth(page, info.project.name);
    await screen.open(page, fakeApi);

    const height = await goblinHeight(page);
    const project = info.project.name === "mobile" ? "mobile" : "desktop";
    expect(height).toBeGreaterThanOrEqual(MIN_HEIGHT_PX[project]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      page.viewportSize()?.width ?? 0,
    );
  });
}

test("Execução concluída no celular: o Baixar fica na primeira tela, acima da goblin", async ({
  page,
  fakeApi,
}, info) => {
  test.skip(info.project.name !== "mobile", "só o celular empilha o cartão de estado");
  await page.setViewportSize(PHONE);
  await new ExecucaoPage(page).open(fakeApi.seedRun({ status: "succeeded" }));

  const download = page.getByRole("link", { name: /^Baixar csv-janitor\.zip/ });
  await expect(download).toBeInViewport({ ratio: 1 });
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
    "Nyx",
  ]);
  for (const image of await cast.locator("img").all()) {
    await image.scrollIntoViewIfNeeded();
    await expect
      .poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth))
      .toBeGreaterThan(0);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
    page.viewportSize()?.width ?? 0,
  );
});
