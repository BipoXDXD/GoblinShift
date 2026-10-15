import { describe, expect, it } from "vitest";

import { budgetViolations, type BuiltFile } from "./check-budget";

const KB = 1024;
const INDEX = `<script src="/theme-init.js"></script>
<link rel="preload" as="font" type="font/woff2" crossorigin href="/assets/bricolage-grotesque-latin-wght-normal-x.woff2">
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
    expect(
      budgetViolations([...WITHIN, file("assets/Lazy-x.js", 200 * KB, 240 * KB)], INDEX),
    ).toEqual([]);
  });

  describe("tamanho cru de cada chunk (divisão por rota)", () => {
    it("chunk de 250 KB crus ainda cabe", () => {
      expect(
        budgetViolations([...WITHIN, file("assets/Rota-x.js", 80 * KB, 250 * KB)], INDEX),
      ).toEqual([]);
    });

    it("um byte acima de 250 KB crus passa do teto, mesmo fora do carregamento inicial", () => {
      expect(
        budgetViolations([...WITHIN, file("assets/Rota-x.js", 80 * KB, 250 * KB + 1)], INDEX),
      ).toEqual(["Chunk assets/Rota-x.js: 250,0 KB crus passa do teto de 250 KB"]);
    });

    it("vale também para o chunk de entrada", () => {
      const files = WITHIN.map((entry) =>
        entry.path === "assets/index-abc.js" ? file(entry.path, 120 * KB, 251 * KB) : entry,
      );
      expect(budgetViolations(files, INDEX)).toEqual([
        "Chunk assets/index-abc.js: 251,0 KB crus passa do teto de 250 KB",
      ]);
    });

    it("o chunk do 3D fica de fora: ele tem o teto próprio do ADR 027", () => {
      expect(
        budgetViolations([...WITHIN, file("assets/oficina-3d-x.js", 200 * KB, 700 * KB)], INDEX),
      ).toEqual([]);
    });
  });

  it("exige o preload da fonte do título", () => {
    const withoutPreload = INDEX.replace(/<link rel="preload"[^>]*>\n/, "");
    expect(budgetViolations(WITHIN, withoutPreload)).toContain(
      "index.html sem preload da fonte do título (bricolage-grotesque latin)",
    );
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

    it("cada GLB até 2048 KB (ADR 030) e a imagem estática até 90 KB", () => {
      const within = [
        ...WITHIN,
        file("assets/oficina-3d-rubia-x.glb", 1900 * KB, 2048 * KB),
        file("assets/oficina-3d-rubia-640w-x.avif", 90 * KB),
      ];
      expect(budgetViolations(within, INDEX)).toEqual([]);

      const over = [
        ...WITHIN,
        file("assets/oficina-3d-rubia-x.glb", 1900 * KB, 2048 * KB + 1),
        file("assets/oficina-3d-poster-x.avif", 91 * KB),
      ];
      expect(budgetViolations(over, INDEX)).toEqual([
        "Modelo 3D (assets/oficina-3d-rubia-x.glb): 2048,0 KB passa do teto de 2048 KB",
        "Imagem estática do 3D (assets/oficina-3d-poster-x.avif): 91,0 KB passa do teto de 90 KB",
      ]);
    });
  });

  describe("ilustrações dos estados (ADR 030)", () => {
    it("cada AVIF ou WebP fora do 3D até 20 KB", () => {
      expect(
        budgetViolations(
          [
            ...WITHIN,
            file("assets/lua-256w-x.avif", 20 * KB),
            file("assets/lua-256w-x.webp", 20 * KB),
          ],
          INDEX,
        ),
      ).toEqual([]);
      expect(
        budgetViolations([...WITHIN, file("assets/ada-256w-x.webp", 20 * KB + 1)], INDEX),
      ).toEqual(["Ilustração (assets/ada-256w-x.webp): 20,0 KB passa do teto de 20 KB"]);
    });
  });
});
