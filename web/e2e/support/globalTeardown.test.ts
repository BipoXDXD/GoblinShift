import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import { removeOwnArtifactsDir } from "./artifactsDir.ts";

// Sem o globalTeardown ligado no config, cada execução local deixa uma goblinshift-e2e-* no TMPDIR
// (issue #420); nada mais na suíte falharia se a linha sumisse (issue #442).
const E2E_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TEARDOWN_MODULE = resolve(E2E_DIR, "globalTeardown.ts");
const SCOPED_VARIABLES = [
  "E2E_BASE_URL",
  "E2E_VPS",
  "E2E_ARTIFACTS_DIR",
  "E2E_ARTIFACTS_DIR_OWNER",
  "GS_E2E_API_KEY",
  "GS_E2E_API_KEYS",
];

async function loadLocalConfig() {
  vi.resetModules();
  for (const name of SCOPED_VARIABLES) {
    vi.stubEnv(name, undefined);
  }
  const { default: config } = await import("../playwright.config.ts");
  return config;
}

// A carga do config cria uma pasta de artefatos; o próprio teste não pode deixá-la no TMPDIR.
function restoreEnv(): void {
  removeOwnArtifactsDir(process.env, process.pid);
  vi.unstubAllEnvs();
}

// A 1ª carga do config paga o @playwright/test a frio; feita na coleta, que não tem timeout, deixa aos
// testes só a reavaliação do config (o mesmo motivo de traceConfig.test.ts).
await loadLocalConfig();
restoreEnv();

describe("globalTeardown do Playwright", () => {
  afterEach(() => {
    restoreEnv();
  });

  it("aponta, no modo local, para o módulo que apaga a pasta de artefatos", async () => {
    const config = await loadLocalConfig();

    expect(config.globalTeardown).toBeTypeOf("string");
    expect(resolve(E2E_DIR, String(config.globalTeardown))).toBe(TEARDOWN_MODULE);
  });

  it("apaga a pasta de artefatos que a carga do config criou", async () => {
    await loadLocalConfig();
    const artifactsDir = process.env["E2E_ARTIFACTS_DIR"] ?? "";
    expect(existsSync(artifactsDir)).toBe(true);

    const { default: globalTeardown } = await import("../globalTeardown.ts");
    globalTeardown();

    expect(existsSync(artifactsDir)).toBe(false);
  });
});
