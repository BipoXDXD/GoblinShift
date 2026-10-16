// A matriz de telas × estados que as specs de contraste, foco, movimento e CSP percorrem. Cada
// entrada abre a tela e espera o estado ficar visível; os estados de execução vêm semeados na API
// falsa, porque a API real não para num estado sob encomenda.
import type { Page } from "@playwright/test";

import { CSV_FILES } from "../fixtures/files";
import { AcessoPage } from "../pages/AcessoPage";
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
export function seedRunHistory(api: FakeApi, extra = 0): number {
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
  {
    name: "Bancada (Media Mill)",
    open: async (page: Page): Promise<void> => {
      await new BancadaPage(page).open("media-mill");
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
    "Execução concluída com avisos e rejeição (detalhe por arquivo)",
    {
      status: "succeeded_with_warnings",
      progress: { phase: null, processed: 3, total: 3 },
      summary: { inputs: 3, ok: 2, rejected: 1, duplicates: 0, outputs: 2 },
      items: [
        {
          position: 0,
          upload_id: "7d3c2a1e-0000-4000-8000-000000000001",
          original_name: "clientes.csv",
          status: "ok",
          error_code: null,
          warnings: ["CSV_FORMULA_CELLS"],
          notices: [],
          counts: { records_read: 1200, filtered_out: 3, written: 1197 },
          duplicate_of: null,
          formula_cells: {
            columns: [
              { name: "saldo", cells: 14 },
              { name: "observação", cells: 1 },
            ],
            more_columns: 2,
          },
        },
        {
          position: 1,
          upload_id: "7d3c2a1e-0000-4000-8000-000000000002",
          original_name: null,
          status: "rejected",
          error_code: "CSV_STRUCTURE_INVALID",
          warnings: [],
          notices: [],
          counts: {},
          duplicate_of: null,
          formula_cells: null,
        },
        {
          position: 2,
          upload_id: "7d3c2a1e-0000-4000-8000-000000000003",
          original_name: null,
          status: "ok",
          error_code: null,
          warnings: [],
          notices: [],
          counts: { records_read: 10, written: 10 },
          duplicate_of: null,
          formula_cells: null,
        },
      ],
    },
    "Arquivo 2",
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
    name: "Bancada sem sessão (convite para entrar)",
    open: async (page: Page, api: FakeApi): Promise<void> => {
      api.account.apiKeyValid = false;
      await page.goto("/bancada/csv-janitor");
      await expect(page.getByRole("region", { name: "Entre para continuar" })).toBeVisible();
    },
  },
  {
    name: "Entrar",
    open: async (page: Page): Promise<void> => {
      await page.goto("/entrar");
      await expect(page.getByRole("link", { name: "Entrar com GitHub" })).toBeVisible();
    },
  },
  {
    name: "Login recusado",
    open: async (page: Page): Promise<void> => {
      await page.goto("/?login_error=failed");
      await expect(page.getByRole("alert")).toBeVisible();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    },
  },
  {
    name: "Acesso com sessão e chaves",
    open: async (page: Page, api: FakeApi): Promise<void> => {
      api.account.seedKey();
      api.account.seedKey({
        revoked_at: "2026-10-05T12:00:00Z",
        last_used_at: "2026-10-04T12:00:00Z",
      });
      api.account.seedKey({ expires_at: "2026-10-02T12:00:00Z" });
      await api.signIn();
      await new AcessoPage(page).open();
      await expect(new AcessoPage(page).keyItems()).toHaveCount(3);
    },
  },
  {
    name: "Acesso com a chave recém-criada",
    open: async (page: Page, api: FakeApi): Promise<void> => {
      await api.signIn();
      const acesso = new AcessoPage(page);
      await acesso.open();
      await expect(await acesso.createKey(["Ler arquivos (files:read)"])).toBeVisible();
    },
  },
  {
    name: "Acesso: confirmar a exclusão da conta",
    open: async (page: Page, api: FakeApi): Promise<void> => {
      await api.signIn();
      const acesso = new AcessoPage(page);
      await acesso.open();
      await expect(await acesso.askDeleteAccount()).toBeVisible();
    },
  },
  {
    name: "Aceite dos termos",
    open: async (page: Page, api: FakeApi): Promise<void> => {
      await api.signIn({ termsAccepted: false });
      await page.goto("/historico");
      await expect(page.getByRole("button", { name: "Aceitar e continuar" })).toBeVisible();
    },
  },
  {
    name: "Termos de Uso",
    open: async (page: Page): Promise<void> => {
      await page.goto("/termos");
      await expect(page.getByRole("complementary", { name: "Rascunho" })).toBeVisible();
    },
  },
  {
    name: "Guia da API",
    open: async (page: Page): Promise<void> => {
      await page.goto("/acesso/guia-da-api");
      await expect(page.getByRole("list", { name: "Passo a passo" })).toBeVisible();
    },
  },
  {
    name: "Créditos",
    open: async (page: Page): Promise<void> => {
      await page.goto("/creditos");
      await expect(page.getByRole("heading", { level: 1, name: "Créditos" })).toBeVisible();
    },
  },
  {
    name: "Aviso de Privacidade",
    open: async (page: Page): Promise<void> => {
      await page.goto("/privacidade");
      await expect(page.getByRole("complementary", { name: "Rascunho" })).toBeVisible();
    },
  },
  {
    name: "Exclusão pedida",
    open: async (page: Page): Promise<void> => {
      await page.goto("/conta-excluida");
      await expect(page.getByRole("heading", { level: 1, name: "Exclusão pedida" })).toBeVisible();
    },
  },
  {
    name: "Página não encontrada",
    open: async (page: Page): Promise<void> => {
      await page.goto("/nao-existe");
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("Página não encontrada");
    },
  },
];
