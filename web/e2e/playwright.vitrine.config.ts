// Captura da vitrine de portfólio (plano 2026-11-22-vitrine-de-portfolio, Tarefa 3): grava os PNGs,
// os quadros do GIF e o manifesto em docs/img/vitrine/. Manual (`npm run vitrine`), fora do CI.
// Só o build local com a API falsa: o ambiente de outro modo é recusado antes de subir o navegador.
import { defineConfig, devices } from "@playwright/test";

import base from "./playwright.config";
import { assertVitrineEnv } from "./vitrine/vitrine";

// Ao carregar a config, antes de o runner subir o servidor ou o navegador.
assertVitrineEnv(process.env);

export default defineConfig({
  ...base,
  testDir: "vitrine",
  testMatch: /vitrine\.spec\.ts$/,
  testIgnore: [],
  outputDir: "../test-results-vitrine",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  projects: [
    {
      name: "vitrine",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 800 },
        colorScheme: "dark",
        reducedMotion: "reduce",
        locale: "pt-BR",
        timezoneId: "America/Sao_Paulo",
      },
    },
  ],
});
