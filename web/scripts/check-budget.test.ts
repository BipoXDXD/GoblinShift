import { describe, expect, it } from "vitest";

import { budgetViolations, type BuiltFile } from "./check-budget";

const KB = 1024;
const INDEX = `<script src="/theme-init.js"></script>
<link rel="preload" as="font" type="font/woff2" crossorigin href="/assets/bricolage-grotesque-latin-wght-normal-x.woff2">
<link rel="preload" as="font" type="font/woff2" crossorigin href="/assets/atkinson-hyperlegible-next-latin-wght-normal-x.woff2">
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
    const withoutPreload = INDEX.replace(/<link rel="preload"[^>]*bricolage[^>]*>\n/, "");
    expect(budgetViolations(WITHIN, withoutPreload)).toEqual([
      "index.html sem preload da fonte do título (bricolage-grotesque latin)",
    ]);
  });

  it("exige o preload da fonte do texto, que é optional (ADR 040)", () => {
    const withoutPreload = INDEX.replace(/<link rel="preload"[^>]*atkinson[^>]*>\n/, "");
    expect(budgetViolations(WITHIN, withoutPreload)).toEqual([
      "index.html sem preload da fonte do texto (atkinson-hyperlegible-next latin)",
    ]);
  });

  it("não aceita preload de fonte sem crossorigin", () => {
    const withoutCors = INDEX.replace(/crossorigin (href="\/assets\/atkinson)/, "$1");
    expect(budgetViolations(WITHIN, withoutCors)).toEqual([
      "index.html sem preload da fonte do texto (atkinson-hyperlegible-next latin)",
    ]);
  });

  it.each([
    ["as", /as="font" /],
    ["type", /type="font\/woff2" /],
  ])("não aceita preload de fonte sem o atributo %s", (_attribute, attribute) => {
    const atkinsonPreload = /<link rel="preload"[^>]*atkinson[^>]*>/;
    const tag = atkinsonPreload.exec(INDEX)?.[0] ?? "";
    const withoutAttribute = INDEX.replace(tag, tag.replace(attribute, ""));
    expect(budgetViolations(WITHIN, withoutAttribute)).toEqual([
      "index.html sem preload da fonte do texto (atkinson-hyperlegible-next latin)",
    ]);
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

    it("o GLB da Rúbia até 8192 KB (ADR 030, 2026-11-22)", () => {
      expect(
        budgetViolations(
          [...WITHIN, file("assets/oficina-3d-rubia-x.glb", 1 * KB, 8192 * KB)],
          INDEX,
        ),
      ).toEqual([]);
      expect(
        budgetViolations(
          [...WITHIN, file("assets/oficina-3d-rubia-x.glb", 1 * KB, 8192 * KB + 1)],
          INDEX,
        ),
      ).toEqual(["Modelo 3D (assets/oficina-3d-rubia-x.glb): 8192,0 KB passa do teto de 8192 KB"]);
    });

    it.each([
      ["assets/oficina-3d-nyx-640w-x.avif", 90],
      ["assets/oficina-3d-nyx-960w-x.avif", 120],
      ["assets/oficina-3d-poster-x.avif", 90],
    ])("imagem estática %s cabe até %i KB e falha um byte acima", (path, kb) => {
      expect(budgetViolations([...WITHIN, file(path, kb * KB)], INDEX)).toEqual([]);
      expect(budgetViolations([...WITHIN, file(path, kb * KB + 1)], INDEX)).toEqual([
        `Imagem estática do 3D (${path}): ${String(kb)},0 KB passa do teto de ${String(kb)} KB`,
      ]);
    });
  });

  describe("elenco em 3D (ADR 038)", () => {
    const RUBIA = file("assets/oficina-3d-rubia-x.glb", 1900 * KB, 3000 * KB);

    it("cada GLB das outras goblins até 6144 KB", () => {
      expect(
        budgetViolations(
          [...WITHIN, RUBIA, file("assets/oficina-3d-jade-x.glb", 1 * KB, 6144 * KB)],
          INDEX,
        ),
      ).toEqual([]);
      expect(
        budgetViolations(
          [...WITHIN, RUBIA, file("assets/oficina-3d-jade-x.glb", 1 * KB, 6144 * KB + 1)],
          INDEX,
        ),
      ).toEqual(["Modelo 3D (assets/oficina-3d-jade-x.glb): 6144,0 KB passa do teto de 6144 KB"]);
    });

    it("o 3D inicial soma o chunk, a Rúbia, a imagem estática de 960 px dela e as miniaturas, até 8704 KB", () => {
      const initial = [
        ...WITHIN,
        file("assets/oficina-3d-x.js", 170 * KB),
        file("assets/oficina-3d-rubia-x.glb", 1 * KB, 8192 * KB),
        file("assets/oficina-3d-rubia-960w-x.avif", 100 * KB),
        // As outras imagens estáticas da Rúbia só vêm por srcset no lugar da de 960 px: não contam.
        file("assets/oficina-3d-rubia-960w-x.webp", 110 * KB),
        file("assets/oficina-3d-rubia-640w-x.avif", 90 * KB),
        ...["rubia", "fagulha", "ferrugem", "jade", "lua", "nyx"].map((id) =>
          file(`assets/oficina-3d-${id}-192w-x.avif`, 40 * KB),
        ),
        // Os modelos das outras goblins só vêm quando a pessoa as escolhe: não contam.
        file("assets/oficina-3d-nyx-x.glb", 1 * KB, 5000 * KB),
      ];
      expect(budgetViolations(initial, INDEX)).toEqual([]);

      const heavier = initial.map((built) =>
        built.path === "assets/oficina-3d-lua-192w-x.avif" ? file(built.path, 43 * KB) : built,
      );
      expect(budgetViolations(heavier, INDEX)).toEqual([
        "3D inicial (chunk, Rúbia, imagem estática e miniaturas): 8705,0 KB passa do teto de 8704 KB",
      ]);
    });

    it("com modelos no build, a Rúbia precisa estar entre eles: é ela que abre a página", () => {
      expect(
        budgetViolations(
          [...WITHIN, file("assets/oficina-3d-nyx-x.glb", 1 * KB, 2000 * KB)],
          INDEX,
        ),
      ).toEqual(["3D inicial: falta o modelo da Rúbia (assets/oficina-3d-rubia-*.glb)"]);
    });
  });

  describe("ilustrações das goblins, por largura (ADR 030, 2026-11-22)", () => {
    it.each([
      ["assets/rubia-768w-x.webp", 64],
      ["assets/rubia-768w-x.avif", 64],
      ["assets/rubia-1024w-x.webp", 120],
      ["assets/rubia-1280w-x.avif", 180],
      ["assets/rubia-escudo-144h-x.webp", 64],
    ])("ilustração %s cabe até %i KB e falha um byte acima", (path, kb) => {
      expect(budgetViolations([...WITHIN, file(path, kb * KB)], INDEX)).toEqual([]);
      expect(budgetViolations([...WITHIN, file(path, kb * KB + 1)], INDEX)).toEqual([
        `Ilustração (${path}): ${String(kb)},0 KB passa do teto de ${String(kb)} KB`,
      ]);
    });
  });
});
