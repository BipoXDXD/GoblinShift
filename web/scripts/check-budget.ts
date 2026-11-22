// Orçamento de peso do build (plano de design, P8; ADR 027, 030 e 038 para o 3D da abertura e as goblins). Roda depois do
// `vite build` no job `web` do CI: `node scripts/check-budget.ts` sai com 1 se algum teto passar.
// JS e CSS contam gzip (o que trafega); fontes, imagens e modelos já vêm comprimidos e contam o
// tamanho do arquivo.
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

export interface BuiltFile {
  /** Caminho relativo a `dist/`, como `assets/index-abc.js`. */
  readonly path: string;
  readonly bytes: number;
  readonly gzipBytes: number;
}

const KB = 1024;
const LIMITS_KB = {
  initialJs: 180,
  /** Por chunk, sem gzip: o custo de parse e compilação cresce com o tamanho cru (divisão por rota). */
  chunkRaw: 250,
  css: 25,
  latinFonts: 100,
  threeDChunk: 300,
  /**
   * ADR 030 (revisto em 2026-11-22): a Rúbia do Hi3D Quality, com textura de 4096 px, mede
   * 7309 KB (1,31 milhão de triângulos); o teto deixa folga até 8 MiB.
   */
  threeDModel: 8192,
  /**
   * ADR 030 (revisto em 2026-11-22): cada uma das outras goblins do elenco, na mesma configuração
   * da Rúbia, mede de 5292 KB (Ferrugem) a 5621 KB (Fagulha); só baixa quando a pessoa a escolhe
   * no seletor.
   */
  threeDCastModel: 6144,
  /**
   * ADR 038: o que a abertura baixa para o 3D sem a pessoa pedir: o chunk (gzip), o GLB da
   * Rúbia, a imagem estática de 960 px dela em AVIF e as miniaturas do seletor. Medido em
   * 2026-11-22: chunk 159 KB gzip + Rúbia 7309 KB + imagem 58 KB + seis miniaturas 48 KB =
   * 7574 KB. Teto = teto da Rúbia (8192) + 10% sobre o resto (265 KB), arredondado para cima em
   * múltiplos de 64 KB.
   */
  threeDInitial: 8512,
} as const;

/**
 * Teto de cada arquivo pela largura no nome (`-<N>w-`): o navegador baixa uma só largura por
 * imagem (srcset e sizes), e a de 3× tem muito mais área que a de 1×. Cada faixa vale até a
 * largura dela; sem sufixo de largura, vale a primeira. Acima da última não há teto: o arquivo
 * falha até alguém decidir quanto ele pode pesar.
 */
type Band = readonly [maxWidth: number, limitKb: number];
type WidthLimits = readonly [Band, ...Band[]];

/**
 * Goblin de um estado ou do elenco, AVIF ou WebP. Era 64 KB para todas as larguras (a maior,
 * a Rúbia em WebP de 768 px, tinha 61 KB); a arte do Hi3D Quality ganha 1024 e 1280 px. Medido
 * em 2026-11-22, o maior arquivo de cada largura é da Rúbia em WebP: 56 KB (768), 77 KB (1024)
 * e 98 KB (1280).
 */
const ILLUSTRATION_LIMITS: WidthLimits = [
  [768, 64],
  [1024, 120],
  [1280, 180],
];
/**
 * Imagem estática do 3D (e miniatura do seletor). Hoje a abertura usa até 640 px; a de 960 px é a
 * do palco maior (PR C), e o teto já a reserva. Medido em 2026-11-22, o maior arquivo é da Rúbia
 * em WebP: 45 KB (640) e 71 KB (960).
 */
const POSTER_LIMITS: WidthLimits = [
  [640, 90],
  [960, 120],
];

/** O teto em KB, ou `null` quando a largura passa da maior faixa. */
function limitByWidth(path: string, limits: WidthLimits): number | null {
  const width = /-(\d+)w-/.exec(path)?.[1];
  if (width === undefined) {
    return limits[0][1];
  }
  return limits.find(([maxWidth]) => Number(width) <= maxWidth)?.[1] ?? null;
}

function overWidthLimit(what: string, file: BuiltFile, limits: WidthLimits): string | null {
  const limitKb = limitByWidth(file.path, limits);
  if (limitKb === null) {
    const maxWidth = Math.max(...limits.map(([width]) => width));
    return `${what}: a largura passa da maior faixa com teto, de ${String(maxWidth)} px`;
  }
  return overLimit({ what, bytes: file.bytes, limitKb, unit: "KB" });
}

/** Chunk e imagem estática do 3D seguem este prefixo no nome (ADR 027). */
const THREE_D_PREFIX = "assets/oficina-3d";
/** A goblin que abre a página (ADR 038): o modelo dela tem teto próprio e entra no 3D inicial. */
const FIRST_MODEL = /^assets\/oficina-3d-rubia-[^/]*\.glb$/;
const FIRST_POSTER = /^assets\/oficina-3d-rubia-960w-[^/]*\.avif$/;
const THUMBNAIL = /^assets\/oficina-3d-[a-z]+-192w-[^/]*\.avif$/;
const THREE_D_CHUNK = /^assets\/oficina-3d-[^/]*\.js$/;

/**
 * Fontes com preload: a do título da primeira tela (CSSD 12.5) e a do texto, que é `optional` e só
 * aparece se chegar antes da primeira pintura (ADR 040). As outras chegam por swap.
 */
export const FONT_PRELOADS = [
  {
    what: "fonte do título (bricolage-grotesque latin)",
    file: /bricolage-grotesque-latin-wght-normal-[^/]*\.woff2$/,
  },
  {
    what: "fonte do texto (atkinson-hyperlegible-next latin)",
    file: /atkinson-hyperlegible-next-latin-wght-normal-[^/]*\.woff2$/,
  },
] as const;

/** Cada teto que passou, em texto para o log do CI; lista vazia quando está tudo dentro. */
export function budgetViolations(files: readonly BuiltFile[], indexHtml: string): string[] {
  const referenced = referencedAssets(indexHtml);
  const initial = files.filter((file) => referenced.has(file.path));
  const violations = [
    overLimit({
      what: "JS inicial",
      bytes: sum(initial, ".js", "gzipBytes"),
      limitKb: LIMITS_KB.initialJs,
      unit: "KB gzip",
    }),
    overLimit({
      what: "CSS",
      bytes: sum(initial, ".css", "gzipBytes"),
      limitKb: LIMITS_KB.css,
      unit: "KB gzip",
    }),
    overLimit({
      what: "Fontes (latin)",
      bytes: files
        .filter((file) => /-latin-wght-normal-[^/]*\.woff2$/.test(file.path))
        .reduce((total, file) => total + file.bytes, 0),
      limitKb: LIMITS_KB.latinFonts,
      unit: "KB",
    }),
  ];
  for (const font of FONT_PRELOADS) {
    if (!hasFontPreload(indexHtml, font.file)) {
      violations.push(`index.html sem preload da ${font.what}`);
    }
  }
  violations.push(...initialThreeDViolations(files));
  for (const file of files) {
    violations.push(
      chunkRawViolation(file),
      illustrationViolation(file),
      ...threeDViolations(file, referenced),
    );
  }
  return violations.filter((violation) => violation !== null);
}

/** O chunk do 3D fica de fora: ele tem o teto próprio do ADR 027. */
function chunkRawViolation(file: BuiltFile): string | null {
  if (!file.path.endsWith(".js") || file.path.startsWith(THREE_D_PREFIX)) {
    return null;
  }
  return overLimit({
    what: `Chunk ${file.path}`,
    bytes: file.bytes,
    limitKb: LIMITS_KB.chunkRaw,
    unit: "KB crus",
  });
}

/** A imagem estática do 3D fica de fora: ela tem o teto próprio. */
function illustrationViolation(file: BuiltFile): string | null {
  if (!/\.(avif|webp)$/.test(file.path) || file.path.startsWith(THREE_D_PREFIX)) {
    return null;
  }
  return overWidthLimit(`Ilustração (${file.path})`, file, ILLUSTRATION_LIMITS);
}

function threeDViolations(file: BuiltFile, referenced: ReadonlySet<string>): (string | null)[] {
  if (file.path.endsWith(".glb")) {
    return [
      overLimit({
        what: `Modelo 3D (${file.path})`,
        bytes: file.bytes,
        limitKb: FIRST_MODEL.test(file.path) ? LIMITS_KB.threeDModel : LIMITS_KB.threeDCastModel,
        unit: "KB",
      }),
    ];
  }
  if (!file.path.startsWith(THREE_D_PREFIX)) {
    return [];
  }
  if (file.path.endsWith(".js")) {
    return [
      overLimit({
        what: `3D (${file.path})`,
        bytes: file.gzipBytes,
        limitKb: LIMITS_KB.threeDChunk,
        unit: "KB gzip",
      }),
      referenced.has(file.path)
        ? `3D (${file.path}) está no index.html: precisa carregar depois do conteúdo`
        : null,
    ];
  }
  return [overWidthLimit(`Imagem estática do 3D (${file.path})`, file, POSTER_LIMITS)];
}

/**
 * O 3D que a abertura baixa sem a pessoa pedir (ADR 038): só vale com modelos no build, e a
 * Rúbia tem de estar entre eles, com a imagem estática de 960 px dela. As outras goblins não
 * contam: o modelo delas vem sob demanda.
 */
function initialThreeDViolations(files: readonly BuiltFile[]): (string | null)[] {
  if (!files.some((file) => file.path.endsWith(".glb"))) {
    return [];
  }
  const firstModel = files.filter((file) => FIRST_MODEL.test(file.path));
  if (firstModel.length === 0) {
    return ["3D inicial: falta o modelo da Rúbia (assets/oficina-3d-rubia-*.glb)"];
  }
  if (!files.some((file) => FIRST_POSTER.test(file.path))) {
    return [
      "3D inicial: falta a imagem estática de 960 px da Rúbia (assets/oficina-3d-rubia-960w-*.avif)",
    ];
  }
  const bytes = (pattern: RegExp, field: "bytes" | "gzipBytes"): number =>
    files.filter((file) => pattern.test(file.path)).reduce((total, file) => total + file[field], 0);
  return [
    overLimit({
      what: "3D inicial (chunk, Rúbia, imagem estática e miniaturas)",
      bytes:
        bytes(THREE_D_CHUNK, "gzipBytes") +
        bytes(FIRST_MODEL, "bytes") +
        bytes(FIRST_POSTER, "bytes") +
        bytes(THUMBNAIL, "bytes"),
      limitKb: LIMITS_KB.threeDInitial,
      unit: "KB",
    }),
  ];
}

/** Arquivos que o `index.html` carrega antes de tudo: scripts, modulepreload e folhas de estilo. */
function referencedAssets(indexHtml: string): Set<string> {
  const urls = [...indexHtml.matchAll(/(?:src|href)="\/([^"]+)"/g)].map(([, url]) => url ?? "");
  return new Set(urls);
}

function hasFontPreload(indexHtml: string, fontFile: RegExp): boolean {
  return [...indexHtml.matchAll(/<link\b[^>]*rel="preload"[^>]*>/g)].some(([tag]) => {
    const href = /href="\/([^"]+)"/.exec(tag)?.[1];
    // Sem `as="font"` e `crossorigin` o preload não casa com o pedido da @font-face e a fonte baixa
    // duas vezes; o `type` deixa o navegador pular um formato que não suporta.
    return (
      href !== undefined &&
      fontFile.test(href) &&
      tag.includes('as="font"') &&
      tag.includes('type="font/woff2"') &&
      tag.includes("crossorigin")
    );
  });
}

function sum(files: readonly BuiltFile[], extension: string, field: "bytes" | "gzipBytes"): number {
  return files
    .filter((file) => file.path.endsWith(extension))
    .reduce((total, file) => total + file[field], 0);
}

interface Measure {
  readonly what: string;
  readonly bytes: number;
  readonly limitKb: number;
  readonly unit: string;
}

function overLimit({ what, bytes, limitKb, unit }: Measure): string | null {
  if (bytes <= limitKb * KB) {
    return null;
  }
  const measured = (bytes / KB).toFixed(1).replace(".", ",");
  return `${what}: ${measured} ${unit} passa do teto de ${String(limitKb)} KB`;
}

function builtFiles(dist: string): BuiltFile[] {
  return readdirSync(dist, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => {
      const content = readFileSync(join(entry.parentPath, entry.name));
      return {
        path: relative(dist, join(entry.parentPath, entry.name)),
        bytes: content.length,
        gzipBytes: gzipSync(content).length,
      };
    });
}

function main(): void {
  const dist = fileURLToPath(new URL("../dist", import.meta.url));
  const violations = budgetViolations(
    builtFiles(dist),
    readFileSync(join(dist, "index.html"), "utf8"),
  );
  for (const violation of violations) {
    console.error(violation);
  }
  if (violations.length > 0) {
    process.exitCode = 1;
    return;
  }
  console.log("Orçamento de peso: tudo dentro dos tetos.");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main();
}
