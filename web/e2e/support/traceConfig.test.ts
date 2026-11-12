import { afterEach, describe, expect, it, vi } from "vitest";

// O Playwright grava no trace os parâmetros de cada chamada, e a chave da conta entra por
// `route.fallback({ headers })` (apiKey.ts): trace com chave real vira credencial em artefato.
// Só o modo local, com a chave canário da API falsa, pode gravar trace.
const REMOTE_ENV = { E2E_BASE_URL: "http://127.0.0.1:18080", CI: "1" };

async function loadTraceMode(env: Readonly<Record<string, string>>): Promise<unknown> {
  vi.resetModules();
  for (const [name, value] of Object.entries(env)) {
    vi.stubEnv(name, value);
  }
  const { default: config } = await import("../playwright.config.ts");
  return config.use?.trace;
}

describe("trace do Playwright", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("fica desligado contra a pilha real, onde a chave é de verdade", async () => {
    const keys = JSON.stringify({ "journeys.spec.ts": "gs_chave-de-teste" });

    expect(await loadTraceMode({ ...REMOTE_ENV, GS_E2E_API_KEYS: keys })).toBe("off");
  });

  it("fica desligado contra a pilha real com a chave única", async () => {
    expect(await loadTraceMode({ ...REMOTE_ENV, GS_E2E_API_KEY: "gs_chave-de-teste" })).toBe("off");
  });

  it("fica desligado contra a VPS", async () => {
    const env = { ...REMOTE_ENV, E2E_BASE_URL: "https://goblinshift.dev", E2E_VPS: "1" };

    expect(await loadTraceMode(env)).toBe("off");
  });

  it("segue gravando a falha no modo local, com a API falsa e a chave canário", async () => {
    vi.stubEnv("E2E_BASE_URL", "");

    expect(await loadTraceMode({ CI: "1" })).toBe("retain-on-failure");
  });
});
