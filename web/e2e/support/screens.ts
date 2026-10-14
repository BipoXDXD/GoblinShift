// A matriz de telas × estados que as specs de contraste, foco, movimento e CSP percorrem. Cada
// entrada abre a tela e espera o estado ficar visível; os estados de execução vêm semeados na API
// falsa, porque a API real não para num estado sob encomenda.
import type { Page } from "@playwright/test";

import { CSV_FILES } from "../fixtures/files";
import { BancadaPage } from "../pages/BancadaPage";
import { ExecucaoPage } from "../pages/ExecucaoPage";
import { HistoricoPage } from "../pages/HistoricoPage";
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

/**
 * Execuções paradas para as telas de lista: uma de cada estado que a lista pinta diferente, com
 * `extra` concluídas a mais, um minuto mais antigas cada, para passar de uma página. Devolve
 * quantas semeou.
 */
function seedRunHistory(api: FakeApi, extra = 0): number {
  const recent: readonly RunOverrides[] = [
    { status: "running", deadline_at: LATER, recipe_id: "image-forge" },
    { status: "queued", recipe_id: "downloads-detox" },
    { status: "failed", error: { code: "INPUT_INVALID", message: "CSV inválido." } },
    { status: "succeeded_with_warnings", recipe_id: "image-forge" },
    { status: "canceled" },
    { status: "succeeded" },
  ];
  const all = [
    ...recent,
    ...Array.from({ length: extra }, () => ({ status: "succeeded" as const })),
  ];
  all.forEach((overrides, index) => {
    api.seedRun({ ...overrides, created_at: minutesBefore(HISTORY_START, index) });
  });
  return all.length;
}

const HISTORY_START = Date.parse("2026-10-14T11:59:00Z");

function minutesBefore(startMs: number, minutes: number): string {
  return new Date(startMs - minutes * 60_000).toISOString();
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
    name: "Oficina com execuções recentes",
    open: async (page: Page, api: FakeApi): Promise<void> => {
      seedRunHistory(api);
      await page.goto("/");
      await expect(page.getByRole("link", { name: "Ver o histórico completo" })).toBeVisible();
    },
  },
  {
    name: "Histórico vazio",
    open: async (page: Page): Promise<void> => {
      await new HistoricoPage(page).open();
      await expect(page.getByText("Nenhuma execução ainda")).toBeVisible();
    },
  },
  {
    name: "Histórico com Carregar mais",
    open: async (page: Page, api: FakeApi): Promise<void> => {
      seedRunHistory(api, 25);
      const historico = new HistoricoPage(page);
      await historico.open();
      await expect(page.getByRole("button", { name: "Carregar mais" })).toBeVisible();
    },
  },
  {
    name: "Histórico filtrado, sem resultado",
    open: async (page: Page, api: FakeApi): Promise<void> => {
      seedRunHistory(api);
      await new HistoricoPage(page).open("?estado=stopping&periodo=7d");
      await expect(page.getByText("Nenhuma execução com esses filtros")).toBeVisible();
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
