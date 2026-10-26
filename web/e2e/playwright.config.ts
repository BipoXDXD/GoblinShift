import { defineConfig, devices } from "@playwright/test";

import { E2E, LOCAL_PORT, NOTICE_BUILD } from "./support/env";

// O build com aviso (npm run e2e:notice) roda a spec do aviso e, com a faixa em toda tela, as de
// layout shift e de contraste; o build normal não tem aviso e pula a spec dele.
const NOTICE_SPECS = /(notice|layout-shift|contrast)\.spec\.ts$/;
const NOTICE_SPEC = /notice\.spec\.ts$/;
const VPS_SPEC = /vps\.spec\.ts$/;
const GPU_SUITE = /\bcom GPU\b/;
// CI separa a renderização pesada; sem fase explícita, a seleção local segue completa.
const RUN_PHASE = process.env["E2E_PHASE"];
if (RUN_PHASE !== undefined && !["ui", "gpu", "notice"].includes(RUN_PHASE)) {
  throw new Error("E2E_PHASE inválida");
}
if (RUN_PHASE !== undefined && (E2E.remote || (RUN_PHASE === "notice") !== NOTICE_BUILD)) {
  throw new Error("E2E_PHASE exige o build local correspondente à fase");
}

// Contra a VPS (E2E_VPS=1): só as jornadas de leitura, no Chromium e no Firefox, em desktop.
const VPS_PROJECTS = [
  {
    name: "chromium",
    use: {
      ...devices["Desktop Chrome"],
      viewport: { width: 1280, height: 800 },
    },
  },
  {
    name: "firefox",
    use: {
      ...devices["Desktop Firefox"],
      viewport: { width: 1280, height: 800 },
    },
  },
];

// Desktop e celular (~400 px, D8), só Chromium: Firefox fica para o job contra a VPS, fora do
// orçamento do commit stage (plano de fechamento da fatia 7, 4.1).
const LOCAL_PROJECTS = [
  {
    name: "desktop",
    use: {
      ...devices["Desktop Chrome"],
      viewport: { width: 1280, height: 800 },
    },
    ...(RUN_PHASE === "gpu" ? { grep: GPU_SUITE } : {}),
    ...(RUN_PHASE === "ui" ? { grepInvert: GPU_SUITE } : {}),
  },
  {
    name: "mobile",
    use: {
      ...devices["Desktop Chrome"],
      viewport: { width: 400, height: 800 },
      hasTouch: true,
      isMobile: true,
    },
    // Tab e anel de foco são do teclado: no celular, só o que muda com a largura.
    ...(RUN_PHASE === "gpu" ? { grep: GPU_SUITE } : {}),
    grepInvert: RUN_PHASE === "ui" ? /@keyboard|\bcom GPU\b/ : /@keyboard/,
  },
];

function specSelection() {
  if (E2E.vps) {
    return { testMatch: VPS_SPEC };
  }
  return NOTICE_BUILD ? { testMatch: NOTICE_SPECS } : { testIgnore: NOTICE_SPEC };
}

export default defineConfig({
  testDir: "specs",
  ...specSelection(),
  outputDir: RUN_PHASE === undefined ? "../test-results" : `../test-results/${RUN_PHASE}`,
  fullyParallel: true,
  forbidOnly: !!process.env["CI"],
  // Contra a VPS, a rede e a página legal longa no Firefox passam do teto local: uma nova tentativa,
  // que o relatório marca como instável em vez de esconder.
  retries: E2E.vps ? 1 : 0,
  // O runner do GitHub tem 4 vCPUs e o padrão do Playwright usaria só 2; as specs esperam mais a
  // rede falsa e os quadros do que a CPU.
  ...(RUN_PHASE === "gpu" ? { workers: 1 } : process.env["CI"] ? { workers: 4 } : {}),
  // Cada spec tem poucos segundos; um teste preso falha cedo em vez de comer o orçamento.
  timeout: E2E.vps ? 60_000 : 20_000,
  expect: { timeout: 5_000 },
  reporter: process.env["CI"]
    ? [
        ["list"],
        [
          "html",
          {
            outputFolder:
              RUN_PHASE === undefined
                ? "../playwright-report"
                : `../playwright-report/${RUN_PHASE}`,
            open: "never",
          },
        ],
      ]
    : [["list"]],
  use: {
    baseURL: E2E.baseUrl,
    locale: "pt-BR",
    timezoneId: "America/Sao_Paulo",
    // Contra a VPS, nada que guarde os cabeçalhos (a chave da conta de teste): sem trace, vídeo
    // nem screenshot. No local, a chave é um canário e o trace ajuda a depurar.
    trace: E2E.vps ? "off" : "retain-on-failure",
    video: "off",
    screenshot: "off",
    actionTimeout: 5_000,
  },
  projects: E2E.vps ? VPS_PROJECTS : LOCAL_PROJECTS,
  ...(E2E.remote
    ? {}
    : {
        webServer: {
          command: `node e2e/server/serve-dist.ts --port ${String(LOCAL_PORT)} --dist ${process.env["E2E_DIST_DIR"] ?? "dist"} --artifacts ${E2E.artifactsDir ?? ""}`,
          cwd: "..",
          url: `${E2E.baseUrl}/`,
          reuseExistingServer: !process.env["CI"],
          timeout: 10_000,
        },
      }),
});
