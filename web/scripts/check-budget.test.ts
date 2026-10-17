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

    it("o GLB da Rúbia até 3400 KB (ADR 030) e a imagem estática até 90 KB", () => {
      const within = [
        ...WITHIN,
        file("assets/oficina-3d-rubia-x.glb", 1900 * KB, 3400 * KB),
        file("assets/oficina-3d-rubia-640w-x.avif", 90 * KB),
      ];
      expect(budgetViolations(within, INDEX)).toEqual([]);

      const over = [
        ...WITHIN,
        file("assets/oficina-3d-rubia-x.glb", 1900 * KB, 3400 * KB + 1),
        file("assets/oficina-3d-poster-x.avif", 91 * KB),
      ];
      expect(budgetViolations(over, INDEX)).toEqual([
        "Modelo 3D (assets/oficina-3d-rubia-x.glb): 3400,0 KB passa do teto de 3400 KB",
        "Imagem estática do 3D (assets/oficina-3d-poster-x.avif): 91,0 KB passa do teto de 90 KB",
      ]);
    });
  });

  describe("elenco em 3D (ADR 038)", () => {
    const RUBIA = file("assets/oficina-3d-rubia-x.glb", 1900 * KB, 3000 * KB);

    it("cada GLB das outras goblins até 2400 KB", () => {
      expect(
        budgetViolations(
          [...WITHIN, RUBIA, file("assets/oficina-3d-jade-x.glb", 1 * KB, 2400 * KB)],
          INDEX,
        ),
      ).toEqual([]);
      expect(
        budgetViolations(
          [...WITHIN, RUBIA, file("assets/oficina-3d-jade-x.glb", 1 * KB, 2400 * KB + 1)],
          INDEX,
        ),
      ).toEqual(["Modelo 3D (assets/oficina-3d-jade-x.glb): 2400,0 KB passa do teto de 2400 KB"]);
    });

    it("o 3D inicial soma o chunk, a Rúbia, a imagem estática dela e as miniaturas, até 3600 KB", () => {
      const initial = [
        ...WITHIN,
        file("assets/oficina-3d-x.js", 160 * KB),
        RUBIA,
        file("assets/oficina-3d-rubia-640w-x.avif", 40 * KB),
        file("assets/oficina-3d-rubia-640w-x.webp", 50 * KB),
        ...["rubia", "fagulha", "ferrugem", "jade", "nyx"].map((id) =>
          file(`assets/oficina-3d-${id}-192w-x.avif`, 80 * KB),
        ),
        // Os modelos das outras goblins só vêm quando a pessoa as escolhe: não contam.
        file("assets/oficina-3d-nyx-x.glb", 1 * KB, 2000 * KB),
      ];
      expect(budgetViolations(initial, INDEX)).toEqual([]);

      const heavier = initial.map((built) =>
        built.path === "assets/oficina-3d-nyx-192w-x.avif" ? file(built.path, 81 * KB) : built,
      );
      expect(budgetViolations(heavier, INDEX)).toEqual([
        "3D inicial (chunk, Rúbia, imagem estática e miniaturas): 3601,0 KB passa do teto de 3600 KB",
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

  describe("ilustrações dos estados (ADR 030)", () => {
    it("cada AVIF ou WebP fora do 3D até 64 KB", () => {
      expect(
        budgetViolations(
          [
            ...WITHIN,
            file("assets/jade-768w-x.avif", 64 * KB),
            file("assets/jade-768w-x.webp", 64 * KB),
          ],
          INDEX,
        ),
      ).toEqual([]);
      expect(
        budgetViolations([...WITHIN, file("assets/nyx-768w-x.webp", 64 * KB + 1)], INDEX),
      ).toEqual(["Ilustração (assets/nyx-768w-x.webp): 64,0 KB passa do teto de 64 KB"]);
    });
  });
});
