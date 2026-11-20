import { chmodSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { firefoxLaunchOptions } from "./firefox.ts";

const root = mkdtempSync(join(tmpdir(), "gs-firefox-config-"));
afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe("firefoxLaunchOptions", () => {
  it("mantém o navegador empacotado na execução local sem override", () => {
    expect(firefoxLaunchOptions(undefined, false)).toEqual({});
  });
  it("exige o Firefox verificado para jornadas do CI", () => {
    expect(() => firefoxLaunchOptions(undefined, true)).toThrow("GS_FIREFOX_EXECUTABLE");
  });
  it("recusa caminho relativo e arquivo ausente", () => {
    expect(() => firefoxLaunchOptions("firefox", true)).toThrow();
    expect(() => firefoxLaunchOptions(join(root, "missing"), true)).toThrow();
  });
  it("recusa diretório e arquivo sem permissão de execução", () => {
    mkdirSync(root, { recursive: true });
    expect(() => firefoxLaunchOptions(root, true)).toThrow();
    const file = join(root, "firefox");
    writeFileSync(file, "synthetic");
    chmodSync(file, 0o600);
    expect(() => firefoxLaunchOptions(file, true)).toThrow();
  });
  it("seleciona o arquivo executável explicitamente", () => {
    mkdirSync(root, { recursive: true });
    const file = join(root, "firefox");
    writeFileSync(file, "synthetic");
    chmodSync(file, 0o700);
    expect(firefoxLaunchOptions(file, true)).toEqual({ executablePath: file });
  });
});
