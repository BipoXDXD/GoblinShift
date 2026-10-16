import { z } from "zod";

import type { ApiClient } from "../../api/client";
import { parseCsvSample, type CsvSample } from "./csvSample";

/**
 * Teto de páginas do mapa: o PDF Press monta no máximo 400 (pdf_press/page_map.py). Acima disso
 * o corpo não é o que a API promete, e a tela mostra a falha.
 */
const MAX_PAGE_MAP_PAGES = 400;

/**
 * O trecho do mapa de páginas de um documento (ADR 037). O corpo do preview é opaco na spec
 * (`application/octet-stream`), então o formato vem do produtor; `schema_version` 1 só cresce de
 * forma aditiva, e os campos novos são ignorados aqui.
 */
const pageMapSchema = z.object({
  schema_version: z.number().int(),
  path: z.string(),
  pages: z
    .array(
      z.object({
        input_position: z.number().int().nonnegative(),
        page_number: z.number().int().positive(),
      }),
    )
    .max(MAX_PAGE_MAP_PAGES),
});

export type PageMap = z.infer<typeof pageMapSchema>;

/** A amostra CSV não é CSV bem formado: defeito do produtor, mostrado como falha do preview. */
class MalformedPreviewError extends Error {
  constructor() {
    super("preview fora do formato esperado");
    this.name = "MalformedPreviewError";
  }
}

/**
 * Rota do preview de uma saída: recebe o id da saída, não o do preview (1.6). Serve também de
 * `src` do `<img>`, que leva o cookie da sessão como qualquer pedido da mesma origem.
 */
export function artifactPreviewPath(sourceArtifactId: string): string {
  return `/api/v1/artifacts/${encodeURIComponent(sourceArtifactId)}/preview`;
}

/** A amostra CSV da saída, já lida; o texto nunca vira HTML (2.2). */
export async function getCsvPreview(
  client: ApiClient,
  sourceArtifactId: string,
  signal?: AbortSignal,
): Promise<CsvSample> {
  const response = await client.requestText({
    path: artifactPreviewPath(sourceArtifactId),
    ...(signal === undefined ? {} : { signal }),
  });
  const sample = parseCsvSample(response.data);
  if (sample === null) {
    throw new MalformedPreviewError();
  }
  return sample;
}

/** O mapa de páginas do documento; o cliente faz o parse do JSON pelo schema. */
export async function getPageMapPreview(
  client: ApiClient,
  sourceArtifactId: string,
  signal?: AbortSignal,
): Promise<PageMap> {
  const response = await client.request({
    method: "GET",
    path: artifactPreviewPath(sourceArtifactId),
    schema: pageMapSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data;
}
