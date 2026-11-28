import { defineConfig, devices } from "@playwright/test";

import base from "./playwright.config";
import { E2E, NOTICE_KIND } from "./support/env";
import { CDP_TAG, titleTag } from "./support/tags";

if (E2E.remote || NOTICE_KIND !== null) {
  throw new Error("A suíte WebKit do palco exige o build local normal, com API falsa.");
}

// Independente do commit stage Chromium: só o palco, sem duplicar as demais jornadas.
export default defineConfig({
  ...base,
  testDir: ".",
  testMatch: /oficina-3d(?:-mobile)?\.spec\.ts$/,
  testIgnore: [],
  outputDir: "../test-results-webkit",
  workers: 1,
  globalTimeout: 4 * 60_000,
  fullyParallel: false,
  retries: 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: "../playwright-report-webkit", open: "never" }],
    // O guard de pulados (check-no-skips.ts) lê este relatório na CI.
    ["json", { outputFile: "../e2e-results/webkit.json" }],
  ],
  projects: [
    {
      name: "webkit-mobile",
      use: { ...devices["iPhone 13"], reducedMotion: "no-preference" },
      // A pinça nativa via CDP só existe no Chromium.
      grepInvert: titleTag(CDP_TAG),
    },
  ],
});
