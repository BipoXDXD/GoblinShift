import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  assertVitrineEnv,
  cssImageUrls,
  goblinsIn,
  knownGoblins,
  renderManifest,
  RESTRICTED_GOBLINS,
  type VitrineEntry,
} from "./vitrine.ts";

const SHA = "0123456789abcdef0123456789abcdef01234567";
const GOBLIN_ART_DIR = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../src/assets/art/goblins",
);

function entry(arquivo: string): VitrineEntry {
  return { arquivo, tela: "Oficina", viewport: "1280x800", goblins: ["rubia"] };
}

describe("assertVitrineEnv", () => {
  it.each([
    "E2E_BASE_URL",
    "E2E_VPS",
    "E2E_CI_READ",
    "E2E_NOTICE",
    "GS_E2E_API_KEY",
    "GS_E2E_API_KEYS",
  ])("assertVitrineEnv recusa cada variável de modo remoto ou chave: %s", (name) => {
    expect(() => {
      assertVitrineEnv({ [name]: "x" });
    }).toThrow(name);
  });

  it("assertVitrineEnv aceita o ambiente local vazio", () => {
    expect(() => {
      assertVitrineEnv({ E2E_BASE_URL: "" });
    }).not.toThrow();
  });
});

describe("goblins", () => {
  it("goblinsIn acha a goblin pelo nome do asset com hash", () => {
    const known = ["fagulha", "ferrugem", "jade", "nyx", "rubia"];
    expect(
      goblinsIn(
        [
          "http://127.0.0.1:4173/assets/jade-256w-Ab12Cd.avif",
          "http://127.0.0.1:4173/assets/oficina-3d-fagulha-192w-x9.webp",
          "http://127.0.0.1:4173/assets/rubia-escudo-48h-Q1.avif",
          "http://127.0.0.1:4173/assets/index-ZZ.js",
        ],
        known,
      ),
    ).toEqual(["fagulha", "jade", "rubia"]);
  });

  it("goblinsIn não confunde o nome da goblin com o meio de outra palavra", () => {
    expect(goblinsIn(["http://127.0.0.1:4173/assets/alua-1-x.js"], ["lua"])).toEqual([]);
  });

  it("knownGoblins lê os nomes da pasta de arte", () => {
    expect(knownGoblins(GOBLIN_ART_DIR)).toEqual(
      expect.arrayContaining(["ferrugem", "nyx", "rubia"]),
    );
  });

  it("as goblins restritas são Jade e Fagulha (D3 a) e existem na pasta de arte", () => {
    expect(RESTRICTED_GOBLINS).toEqual(["jade", "fagulha"]);
    expect(knownGoblins(GOBLIN_ART_DIR)).toEqual(expect.arrayContaining([...RESTRICTED_GOBLINS]));
  });
});

describe("cssImageUrls", () => {
  it("cssImageUrls lê cada url() de background-image e content, com ou sem aspas", () => {
    expect(
      cssImageUrls([
        'url("http://127.0.0.1:4173/assets/jade-256w-A1.avif"), linear-gradient(red, blue)',
        "url(/assets/fagulha-384w-B2.webp)",
        "url('/assets/lua-256w-C3.webp')",
        "none",
        "normal",
      ]),
    ).toEqual([
      "http://127.0.0.1:4173/assets/jade-256w-A1.avif",
      "/assets/fagulha-384w-B2.webp",
      "/assets/lua-256w-C3.webp",
    ]);
  });

  it("cssImageUrls lê as opções de image-set()", () => {
    expect(
      cssImageUrls(['image-set(url("/a/nyx-1x-Q.avif") 1x, url("/a/nyx-2x-Q.avif") 2x)']),
    ).toEqual(["/a/nyx-1x-Q.avif", "/a/nyx-2x-Q.avif"]);
  });
});

describe("renderManifest", () => {
  it("renderManifest não depende da ordem das entradas", () => {
    const a = entry("receita-pdf-press.png");
    const b = entry("oficina.png");
    expect(renderManifest([a, b], SHA)).toBe(renderManifest([b, a], SHA));
    expect(renderManifest([a], SHA).endsWith("\n")).toBe(true);
  });

  it("renderManifest recusa commit fora do formato", () => {
    expect(() => renderManifest([], "abc")).toThrow();
  });
});
