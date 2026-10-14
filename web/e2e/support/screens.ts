// A matriz de telas × estados que as specs de contraste, foco, movimento e CSP percorrem. Cada
// entrada abre a tela e espera o estado ficar visível; os estados de execução vêm semeados na API
// falsa, porque a API real não para num estado sob encomenda.
import type { Page } from "@playwright/test";

import { CSV_FILES } from "../fixtures/files";
import { BancadaPage } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import type { FakeApi, RunOverrides } from "./fakeApi";
import { expect } from "./test";

export interface Screen {
  readonly name: string;
  readonly open: (page: Page, api: FakeApi) => Promise<void>;
}

const LATER = "2026-10-15T12:00:00Z";

function runScreen(name: string, overrides: RunOverrides, visibleText: string): Screen {
  return {
    name,
    open: async (page: Page, api: FakeApi): Promise<void> => {
      const execucao = new ExecucaoPage(page);
      await execucao.open(api.seedRun(overrides));
      await expect(page.getByText(visibleText, { exact: true }).first()).toBeVisible();
    },
  };
}

export const SCREENS: readonly Screen[] = [
  {
    name: "Oficina",
    open: async (page: Page): Promise<void> => {
      await page.goto("/");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    },
  },
  {
    name: "Bancada vazia (CSV Janitor)",
    open: async (page: Page): Promise<void> => {
      await new BancadaPage(page).open("csv-janitor");
    },
  },
  {
    name: "Bancada com arquivos (CSV Janitor)",
    open: async (page: Page): Promise<void> => {
      const bancada = new BancadaPage(page);
      await bancada.open("csv-janitor");
      await bancada.addFiles(CSV_FILES);
    },
  },
  {
    name: "Bancada com erros (CSV Janitor)",
    open: async (page: Page): Promise<void> => {
      const bancada = new BancadaPage(page);
      await bancada.open("csv-janitor");
      await bancada.runButton().click();
      await expect(page.getByText("Corrija os itens abaixo antes de continuar.")).toBeVisible();
    },
  },
  {
    name: "Bancada (Image Forge)",
    open: async (page: Page): Promise<void> => {
      await new BancadaPage(page).open("image-forge");
    },
  },
  {
    name: "Bancada (Downloads Detox)",
    open: async (page: Page): Promise<void> => {
      await new BancadaPage(page).open("downloads-detox");
    },
  },
  runScreen("Execução na fila", { status: "queued", queue_ahead: 2 }, "Na fila"),
  runScreen(
    "Execução em andamento",
    {
      status: "running",
      deadline_at: LATER,
      progress: { phase: "processing", processed: 1, total: 3 },
    },
    "Em andamento",
  ),
  runScreen(
    "Execução concluída",
    { status: "succeeded", progress: { phase: null, processed: 2, total: 2 } },
    "Concluída",
  ),
  runScreen(
    "Execução que falhou",
    {
      status: "failed",
      error: { code: "INPUT_INVALID", message: "O arquivo clientes-a.csv não é um CSV válido." },
    },
    "Falhou",
  ),
  {
    name: "Página não encontrada",
    open: async (page: Page): Promise<void> => {
      await page.goto("/nao-existe");
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("Página não encontrada");
    },
  },
];
