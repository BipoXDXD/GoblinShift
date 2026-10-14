import { jsonResponse } from "./fakeApi";

// Corpo de GET /api/v1/catalog como a API o devolve hoje (só os trechos do schema que a interface
// lê). Cada teste parte daqui e troca o que precisa com `withVersion`.
const CATALOG_BODY = {
  recipes: [
    {
      recipe_id: "csv-janitor",
      versions: [
        {
          version: 1,
          status: "current",
          limits: {
            max_inputs: 5,
            table_bytes: 8388608,
            records: 20000,
            columns: 100,
            record_bytes: 1048576,
            field_bytes: 65536,
          },
          defaults: {
            case_rules: [],
            deduplicate_by: [],
            filters: [],
            formula_guard: "none",
            input_delimiter: ",",
            output_columns: null,
            output_delimiter: ",",
            trim_columns: [],
          },
          parameters_schema: {
            type: "object",
            properties: {
              filters: { type: "array", maxItems: 5 },
            },
          },
        },
      ],
    },
    {
      recipe_id: "image-forge",
      versions: [
        {
          version: 1,
          status: "current",
          limits: {
            max_inputs: 100,
            max_pixels: 24000000,
            max_axis_px: 8192,
            thumbnail_box_px: 320,
          },
          defaults: {
            jpeg_background: "#FFFFFF",
            jpeg_quality: 85,
            make_thumbnails: false,
            max_height: 1600,
            max_width: 1600,
            output_format: "preserve",
          },
          parameters_schema: {
            type: "object",
            properties: {
              max_width: { type: "integer", minimum: 16, maximum: 4096, default: 1600 },
              max_height: { type: "integer", minimum: 16, maximum: 4096, default: 1600 },
              jpeg_quality: { type: "integer", minimum: 60, maximum: 95, default: 85 },
            },
          },
        },
      ],
    },
    {
      recipe_id: "downloads-detox",
      versions: [
        {
          version: 1,
          status: "current",
          limits: { max_inputs: 100, max_total_input_bytes: 503316480 },
          defaults: {
            deduplicate: true,
            group_by: "kind",
            name_mode: "preserve",
            name_prefix: "file",
          },
          parameters_schema: {
            type: "object",
            properties: {
              name_prefix: { type: "string", minLength: 1, maxLength: 40, default: "file" },
            },
          },
        },
      ],
    },
  ],
} as const;

type CatalogBody = typeof CATALOG_BODY;
type RecipeEntry = CatalogBody["recipes"][number];
type VersionEntry = RecipeEntry["versions"][number];

/** Versão da receita no catálogo de exemplo; lança se o exemplo não a tiver. */
export function catalogVersion(recipeId: RecipeEntry["recipe_id"]): VersionEntry {
  const recipe = CATALOG_BODY.recipes.find((entry) => entry.recipe_id === recipeId);
  const version = recipe?.versions[0];
  if (version === undefined) {
    throw new Error(`receita ${recipeId} ausente do catálogo de exemplo`);
  }
  return version;
}

/** Catálogo de exemplo com a versão de uma receita trocada (ou removida, com `null`). */
export function withVersion(recipeId: string, version: unknown): unknown {
  return {
    recipes: CATALOG_BODY.recipes.map((recipe) =>
      recipe.recipe_id === recipeId
        ? { ...recipe, versions: version === null ? [] : [version] }
        : recipe,
    ),
  };
}

/** Handler de GET /api/v1/catalog para o `fakeApi`. */
export function catalogHandler(body: unknown = CATALOG_BODY): () => Response {
  return () => jsonResponse(body, 200, { ETag: '"catalogo-de-teste"' });
}
