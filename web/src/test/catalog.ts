import { CATALOG_BODY } from "./catalogBody";
import { jsonResponse } from "./fakeApi";

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
