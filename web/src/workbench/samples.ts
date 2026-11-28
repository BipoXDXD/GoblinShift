// Amostras do "Testar com arquivo de exemplo" (D4), geradas por scripts/make_samples.py. O
// `?url&no-inline` faz de cada uma um asset com hash em /assets/: cache imutável, 404 de verdade
// e nunca um `data:`, que a CSP (`connect-src 'self' blob:`) recusaria no fetch.
import pdfA from "./samples/exemplo-a.pdf?url&no-inline";
import pdfB from "./samples/exemplo-b.pdf?url&no-inline";
import clientsCsv from "./samples/exemplo-clientes.csv?url&no-inline";
import clipWebm from "./samples/exemplo-clipe.webm?url&no-inline";
import photoJpg from "./samples/exemplo-downloads/IMG_2041.JPG?url&no-inline";
import screenshotPng from "./samples/exemplo-downloads/captura-de-tela.png?url&no-inline";
import stockCopyCsv from "./samples/exemplo-downloads/estoque(1).csv?url&no-inline";
import stockCsv from "./samples/exemplo-downloads/estoque.csv?url&no-inline";
import toolsZip from "./samples/exemplo-downloads/ferramentas.zip?url&no-inline";
import readmeTxt from "./samples/exemplo-downloads/leia-me.txt?url&no-inline";
import workshopJpg from "./samples/exemplo-oficina.jpg?url&no-inline";

import type { RecipeId } from "../recipes/types";

export interface SampleFile {
  readonly url: string;
  /** Nome com que o arquivo entra na lista da Bancada (e no upload). */
  readonly name: string;
  readonly type: string;
}

/** Uma amostra por receita, cada uma dentro dos limites da versão no catálogo. */
export const SAMPLES: Readonly<Record<RecipeId, readonly SampleFile[]>> = {
  "csv-janitor": [{ url: clientsCsv, name: "exemplo-clientes.csv", type: "text/csv" }],
  "image-forge": [{ url: workshopJpg, name: "exemplo-oficina.jpg", type: "image/jpeg" }],
  "downloads-detox": [
    { url: photoJpg, name: "IMG_2041.JPG", type: "image/jpeg" },
    { url: screenshotPng, name: "captura-de-tela.png", type: "image/png" },
    { url: stockCsv, name: "estoque.csv", type: "text/csv" },
    { url: stockCopyCsv, name: "estoque(1).csv", type: "text/csv" },
    { url: readmeTxt, name: "leia-me.txt", type: "text/plain" },
    { url: toolsZip, name: "ferramentas.zip", type: "application/zip" },
  ],
  "pdf-press": [
    { url: pdfA, name: "exemplo-a.pdf", type: "application/pdf" },
    { url: pdfB, name: "exemplo-b.pdf", type: "application/pdf" },
  ],
  "media-mill": [{ url: clipWebm, name: "exemplo-clipe.webm", type: "video/webm" }],
};

/**
 * Baixa a amostra da receita e devolve os arquivos prontos para o mesmo caminho do upload. Rejeita
 * se algum arquivo não vier (rede, 404): a Bancada mostra o motivo e não entra nada pela metade.
 */
export async function loadSample(
  recipeId: RecipeId,
  fetcher: typeof fetch,
): Promise<readonly File[]> {
  return Promise.all(
    SAMPLES[recipeId].map(async ({ url, name, type }) => {
      const response = await fetcher(url);
      if (!response.ok) {
        throw new Error(`Amostra ${name} indisponível (HTTP ${String(response.status)}).`);
      }
      return new File([await response.blob()], name, { type });
    }),
  );
}
