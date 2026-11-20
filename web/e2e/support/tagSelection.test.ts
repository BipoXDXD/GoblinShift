import type { PlaywrightTestConfig } from "@playwright/test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ACCOUNT_TAG,
  CDP_TAG,
  FAKE_API_TAG,
  KEYBOARD_TAG,
  MOBILE_TAG,
  REAL_API_TAG,
} from "./tags.ts";

// Caso que não pode rodar num projeto sai dele pela config (grep/grepInvert por tag no título), em
// vez de `test.skip` em runtime: assim o guard de pulados (check-no-skips.ts) não tem exceção.
const SCOPED_VARIABLES = [
  "GS_FIREFOX_EXECUTABLE",
  "E2E_BASE_URL",
  "E2E_VPS",
  "E2E_PHASE",
  "GS_E2E_API_KEY",
  "GS_E2E_API_KEYS",
];
const REMOTE_ENV = {
  E2E_BASE_URL: "http://127.0.0.1:18080",
  GS_E2E_API_KEY: "gs_chave-de-teste",
  CI: "1",
};
// These tests inspect config only. Node supplies a valid executable without launching Firefox.
const VPS_ENV = {
  E2E_BASE_URL: "https://goblinshift.dev",
  E2E_VPS: "1",
  CI: "1",
  GS_FIREFOX_EXECUTABLE: process.execPath,
};

type Config = "main" | "webkit";
type Project = NonNullable<PlaywrightTestConfig["projects"]>[number];
type Collects = (project: string, title: string) => boolean;

function matches(pattern: Project["grep"], title: string): boolean {
  const patterns = pattern === undefined ? [] : [pattern].flat();
  return patterns.some((regex) => regex.test(title));
}

async function loadSelection(
  env: Readonly<Record<string, string>>,
  config: Config = "main",
): Promise<Collects> {
  vi.resetModules();
  for (const [name, value] of Object.entries(env)) {
    vi.stubEnv(name, value);
  }
  const module =
    config === "main"
      ? await import("../playwright.config.ts")
      : await import("../playwright.webkit.config.ts");
  const projects = module.default.projects ?? [];
  return (name, title) => {
    const project = projects.find((candidate) => candidate.name === name);
    if (project === undefined) throw new Error(`projeto ${name} ausente da config`);
    const included = project.grep === undefined || matches(project.grep, title);
    return included && !matches(project.grepInvert, title);
  };
}

function clearScopedEnv(): void {
  for (const name of SCOPED_VARIABLES) {
    vi.stubEnv(name, undefined);
  }
}

// Como em traceConfig.test.ts: a 1ª carga do config (o @playwright/test a frio) fica na coleta,
// que não tem timeout.
clearScopedEnv();
await loadSelection({});
vi.unstubAllEnvs();

describe("seleção por tag nos projetos do Playwright", () => {
  beforeEach(() => {
    clearScopedEnv();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("deixa o caso de celular fora do desktop e o de teclado fora do celular", async () => {
    const collects = await loadSelection({ CI: "1" });

    expect(collects("desktop", `pinça no palco ${MOBILE_TAG}`)).toBe(false);
    expect(collects("mobile", `pinça no palco ${MOBILE_TAG}`)).toBe(true);
    expect(collects("mobile", `atalho de teclado ${KEYBOARD_TAG}`)).toBe(false);
    expect(collects("desktop", `atalho de teclado ${KEYBOARD_TAG}`)).toBe(true);
  });

  it("coleta os casos da API falsa no modo local", async () => {
    const collects = await loadSelection({ CI: "1" });

    expect(collects("desktop", `estado forçado ${FAKE_API_TAG}`)).toBe(true);
    expect(collects("mobile", `estado forçado ${FAKE_API_TAG}`)).toBe(true);
  });

  it("deixa os casos da API falsa fora da pilha real", async () => {
    const collects = await loadSelection(REMOTE_ENV);

    expect(collects("desktop", `estado forçado ${FAKE_API_TAG}`)).toBe(false);
    expect(collects("mobile", `estado forçado ${FAKE_API_TAG}`)).toBe(false);
    expect(collects("desktop", "jornada contra a API real")).toBe(true);
  });

  it("deixa o caso da API real fora do modo local e o coleta na pilha real", async () => {
    const local = await loadSelection({ CI: "1" });
    const remote = await loadSelection(REMOTE_ENV);

    expect(local("desktop", `documentação ${REAL_API_TAG}`)).toBe(false);
    expect(local("mobile", `documentação ${REAL_API_TAG}`)).toBe(false);
    expect(remote("desktop", `documentação ${REAL_API_TAG}`)).toBe(true);
    expect(remote("mobile", `documentação ${REAL_API_TAG}`)).toBe(true);
  });

  it("contra a VPS sem a chave, deixa o bloco com conta de fora", async () => {
    const collects = await loadSelection(VPS_ENV);

    expect(collects("chromium", `com conta ${ACCOUNT_TAG} › histórico`)).toBe(false);
    expect(collects("firefox", `com conta ${ACCOUNT_TAG} › histórico`)).toBe(false);
    expect(collects("chromium", `documentação ${REAL_API_TAG}`)).toBe(true);
  });

  it("contra a VPS com a chave, coleta o bloco com conta", async () => {
    const collects = await loadSelection({ ...VPS_ENV, GS_E2E_API_KEY: "gs_chave-de-teste" });

    expect(collects("chromium", `com conta ${ACCOUNT_TAG} › histórico`)).toBe(true);
  });

  it("não confunde uma tag com outra que começa igual", async () => {
    const collects = await loadSelection({ CI: "1" });

    expect(collects("desktop", "caso @mobilex")).toBe(true);
    expect(collects("desktop", `caso ${MOBILE_TAG}-extra`)).toBe(true);
  });

  it("deixa o caso que exige CDP fora do WebKit", async () => {
    const collects = await loadSelection({ CI: "1" }, "webkit");

    expect(collects("webkit-mobile", `pinça no palco ${MOBILE_TAG} ${CDP_TAG}`)).toBe(false);
    expect(collects("webkit-mobile", `palco móvel ${MOBILE_TAG}`)).toBe(true);
  });
});
