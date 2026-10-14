import { z } from "zod";

import type { ApiClient } from "../api/client";

// Espelho em runtime de GET /catalog; api.test.ts trava a igualdade com o tipo gerado da spec.
// Defaults, limites e schema chegam sem tipo fixo: cada receita lê os seus (recipes/*/settings.ts).
const catalogVersionSchema = z.object({
  version: z.int(),
  status: z.enum(["current", "deprecated", "retired"]),
  limits: z.record(z.string(), z.int()),
  defaults: z.record(z.string(), z.unknown()),
  parameters_schema: z.record(z.string(), z.unknown()),
});

const catalogSchema = z.object({
  recipes: z.array(
    z.object({
      recipe_id: z.string(),
      versions: z.array(catalogVersionSchema),
    }),
  ),
});

export type Catalog = z.infer<typeof catalogSchema>;
export type CatalogVersion = z.infer<typeof catalogVersionSchema>;

export const CATALOG_QUERY_KEY = ["catalog"] as const;

/**
 * GET /catalog. A API responde com ETag e `no-cache`: o cache HTTP do navegador revalida a cada
 * leitura e reaproveita o corpo no 304, sem código daqui.
 */
export async function getCatalog(client: ApiClient, signal?: AbortSignal): Promise<Catalog> {
  const response = await client.request({
    method: "GET",
    path: "/api/v1/catalog",
    schema: catalogSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data;
}

export function findCatalogVersion(
  catalog: Catalog,
  recipeId: string,
  version: number,
): CatalogVersion | null {
  const recipe = catalog.recipes.find((entry) => entry.recipe_id === recipeId);
  return recipe?.versions.find((entry) => entry.version === version) ?? null;
}
