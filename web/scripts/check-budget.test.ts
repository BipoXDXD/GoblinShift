import { describe, expect, it } from "vitest";

import { budgetViolations, type BuiltFile } from "./check-budget";

const KB = 1024;
const INDEX = `<script src="/theme-init.js"></script>
<script type="module" crossorigin src="/assets/index-abc.js"></script>
<link rel="modulepreload" crossorigin href="/assets/vendor-def.js">
<link rel="stylesheet" crossorigin href="/assets/index-abc.css">`;

function file(path: string, gzipBytes: number, bytes = gzipBytes): BuiltFile {
  return { path, bytes, gzipBytes };
}

const WITHIN = [
  file("assets/index-abc.js", 120 * KB),
  file("assets/vendor-def.js", 50 * KB),
  file("assets/index-abc.css", 5 * KB),
  file("assets/bricolage-grotesque-latin-wght-normal-x.woff2", 41 * KB),
  file("assets/atkinson-hyperlegible-next-latin-wght-normal-x.woff2", 34 * KB),
  file("assets/atkinson-hyperlegible-next-latin-ext-wght-normal-x.woff2", 19 * KB),
];

describe("budgetViolations", () => {
  it("dentro dos tetos, nada a dizer", () => {
    expect(budgetViolations(WITHIN, INDEX)).toEqual([]);
  });

  it("soma o JS inicial do index.html, com os modulepreload", () => {
    const heavier = [...WITHIN, file("assets/vendor-def.js", 61 * KB)].filter(
      (entry, index, all) => all.findLastIndex((other) => other.path === entry.path) === index,
    );

    expect(budgetViolations(heavier, INDEX)).toEqual([
      "JS inicial: 181,0 KB gzip passa do teto de 180 KB",
    ]);
  });

  it("chunk carregado depois não conta no JS inicial", () => {
    expect(budgetViolations([...WITHIN, file("assets/Lazy-x.js", 500 * KB)], INDEX)).toEqual([]);
  });

  it("CSS acima do teto", () => {
    const files = WITHIN.map((entry) =>
      entry.path.endsWith(".css") ? file(entry.path, 26 * KB) : entry,
    );
    expect(budgetViolations(files, INDEX)).toEqual(["CSS: 26,0 KB gzip passa do teto de 25 KB"]);
  });

  it("conta só o subconjunto latin das fontes, que toda página em pt-BR baixa", () => {
    const files = [
      ...WITHIN,
      file("assets/atkinson-hyperlegible-mono-latin-wght-normal-x.woff2", 26 * KB),
    ];
    expect(budgetViolations(files, INDEX)).toEqual([
      "Fontes (latin): 101,0 KB passa do teto de 100 KB",
    ]);
  });

  describe("3D da abertura (ADR 027)", () => {
    it("chunk do 3D até 300 KB gzip, fora do carregamento inicial", () => {
      expect(
        budgetViolations([...WITHIN, file("assets/oficina-3d-x.js", 300 * KB)], INDEX),
      ).toEqual([]);
      expect(
        budgetViolations([...WITHIN, file("assets/oficina-3d-x.js", 301 * KB)], INDEX),
      ).toEqual(["3D (assets/oficina-3d-x.js): 301,0 KB gzip passa do teto de 300 KB"]);
    });

    it("chunk do 3D no index.html quebra a carga depois do conteúdo", () => {
      const index = `${INDEX}\n<link rel="modulepreload" href="/assets/oficina-3d-x.js">`;
      expect(
        budgetViolations([...WITHIN, file("assets/oficina-3d-x.js", 10 * KB)], index),
      ).toContain(
        "3D (assets/oficina-3d-x.js) está no index.html: precisa carregar depois do conteúdo",
      );
    });

    it("cada GLB até 500 KB e a imagem estática até 90 KB", () => {
      const files = [
        ...WITHIN,
        file("assets/goblins-x.glb", 450 * KB, 501 * KB),
        file("assets/oficina-3d-poster-x.avif", 91 * KB),
      ];
      expect(budgetViolations(files, INDEX)).toEqual([
        "Modelo 3D (assets/goblins-x.glb): 501,0 KB passa do teto de 500 KB",
        "Imagem estática do 3D (assets/oficina-3d-poster-x.avif): 91,0 KB passa do teto de 90 KB",
      ]);
    });
  });
});
