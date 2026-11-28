// Monta o GIF da jornada da vitrine (plano 2026-11-22-vitrine-de-portfolio, Tarefa 2) a partir
// dos quadros que a captura grava em `FRAMES_DIR`. Uso: `node scripts/vitrine-gif.ts`, depois da
// captura. Precisa do FFmpeg no PATH (README, Desenvolvimento local).
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { FRAMES_DIR, VITRINE_DIR } from "../e2e/vitrine/vitrine.ts";

export const FRAME_SECONDS = 1.5;
export const GIF_WIDTH = 960;
export const MIN_FRAMES = 4;

const FRAME_FILE = /^quadro-\d{2}\.png$/;

/** Argumentos do `ffmpeg`: um quadro a cada 1,5 s, paleta de 128 cores e laço infinito. */
export function gifArgs(framesDir: string, out: string): string[] {
  return [
    "-y",
    "-framerate",
    "2/3",
    "-i",
    join(framesDir, "quadro-%02d.png"),
    "-vf",
    "scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
    "-loop",
    "0",
    out,
  ];
}

function countFrames(framesDir: string): number {
  if (!existsSync(framesDir)) {
    return 0;
  }
  return readdirSync(framesDir).filter((file) => FRAME_FILE.test(file)).length;
}

function main(): void {
  const frames = countFrames(FRAMES_DIR);
  if (frames < MIN_FRAMES) {
    console.error(
      `vitrine-gif: ${String(frames)} quadro(s) em ${FRAMES_DIR}; são precisos ao menos ${String(MIN_FRAMES)}. Rode a captura antes.`,
    );
    process.exitCode = 1;
    return;
  }
  mkdirSync(VITRINE_DIR, { recursive: true });
  const out = join(VITRINE_DIR, "jornada.gif");
  const result = spawnSync("ffmpeg", gifArgs(FRAMES_DIR, out), { stdio: "inherit" });
  if (result.error !== undefined) {
    const code = (result.error as NodeJS.ErrnoException).code;
    console.error(
      code === "ENOENT"
        ? "vitrine-gif: instale o FFmpeg (README, Desenvolvimento local)."
        : `vitrine-gif: o FFmpeg não rodou: ${result.error.message}`,
    );
    process.exitCode = 1;
    return;
  }
  if (result.status !== 0) {
    console.error(`vitrine-gif: o FFmpeg saiu com ${String(result.status)}.`);
    process.exitCode = 1;
    return;
  }
  console.log(`vitrine-gif: ${String(frames)} quadros em ${out}`);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main();
}
