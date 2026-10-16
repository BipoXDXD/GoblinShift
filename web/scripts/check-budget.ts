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
   * ADR 030: a Rúbia de 1 milhão de triângulos reduzida a ~240 mil, com texturas de 2048 px em
   * WebP e Meshopt, mede 3283 KB; reduzir mais já tirava relevo do rosto.
   */
  threeDModel: 3400,
  /**
   * ADR 038: cada uma das outras cinco goblins, na mesma configuração da Rúbia, mede de 1492 KB
   * (Nyx) a 2247 KB (Jade); só baixa quando a pessoa a escolhe no seletor.
   */
  threeDCastModel: 2400,
  /**
   * ADR 038: o que a abertura baixa para o 3D sem a pessoa pedir: o chunk (gzip), o GLB da
   * Rúbia, a maior imagem estática dela em AVIF e as cinco miniaturas do seletor. Medido: ~3530 KB.
   */
  threeDInitial: 3600,
  threeDPoster: 90,
  /**
   * Goblin de um estado ou do elenco, cada arquivo (AVIF ou WebP, de 256 a 768 px). Era 20 KB
   * com a caixa de 128×160; em 2026-10-15 o usuário pediu as goblins em destaque (256×320 CSS) e
   * o arquivo de 768 px, o das telas 3×, tem 9× a área. A maior é a Rúbia em WebP, com 61 KB. O
   * navegador baixa um só arquivo por goblin (srcset e sizes), e só quando ela aparece (lazy).
   */
  illustration: 64,
} as const;

/** Chunk e imagem estática do 3D seguem este prefixo no nome (ADR 027). */
const THREE_D_PREFIX = "assets/oficina-3d";
/** A goblin que abre a página (ADR 038): o modelo dela tem teto próprio e entra no 3D inicial. */
const FIRST_MODEL = /^assets\/oficina-3d-rubia-[^/]*\.glb$/;
const FIRST_POSTER = /^assets\/oficina-3d-rubia-640w-[^/]*\.avif$/;
const THUMBNAIL = /^assets\/oficina-3d-[a-z]+-192w-[^/]*\.avif$/;
const THREE_D_CHUNK = /^assets\/oficina-3d-[^/]*\.js$/;

/** A fonte do título da primeira tela: a única com preload (CSSD 12.5); o resto chega por swap. */
export const FONT_PRELOAD = /bricolage-grotesque-latin-wght-normal-[^/]*\.woff2$/;

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
  if (!hasFontPreload(indexHtml)) {
    violations.push("index.html sem preload da fonte do título (bricolage-grotesque latin)");
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
  return overLimit({
    what: `Ilustração (${file.path})`,
    bytes: file.bytes,
    limitKb: LIMITS_KB.illustration,
    unit: "KB",
  });
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
  return [
    overLimit({
      what: `Imagem estática do 3D (${file.path})`,
      bytes: file.bytes,
      limitKb: LIMITS_KB.threeDPoster,
      unit: "KB",
    }),
  ];
}

/**
 * O 3D que a abertura baixa sem a pessoa pedir (ADR 038): só vale com modelos no build, e a
 * Rúbia tem de estar entre eles. As outras goblins não contam: o modelo delas vem sob demanda.
 */
function initialThreeDViolations(files: readonly BuiltFile[]): (string | null)[] {
  if (!files.some((file) => file.path.endsWith(".glb"))) {
    return [];
  }
  const firstModel = files.filter((file) => FIRST_MODEL.test(file.path));
  if (firstModel.length === 0) {
    return ["3D inicial: falta o modelo da Rúbia (assets/oficina-3d-rubia-*.glb)"];
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

function hasFontPreload(indexHtml: string): boolean {
  return [...indexHtml.matchAll(/<link\b[^>]*rel="preload"[^>]*>/g)].some(([tag]) => {
    const href = /href="\/([^"]+)"/.exec(tag)?.[1];
    return href !== undefined && FONT_PRELOAD.test(href) && tag.includes("crossorigin");
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
