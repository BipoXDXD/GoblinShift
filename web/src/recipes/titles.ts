import { TEXT } from "../text";
import type { RecipeId } from "./types";

/**
 * Nome de exibição de cada receita, a fonte do `title` do registro. Fica fora do registry.tsx
 * porque as rotas precisam dele já no chunk de entrada (título da aba), e o registro traz os
 * formulários de todas as receitas.
 */
export const RECIPE_TITLES: Readonly<Record<RecipeId, string>> = {
  "csv-janitor": TEXT.csvJanitor.title,
  "image-forge": TEXT.imageForge.title,
  "downloads-detox": TEXT.downloadsDetox.title,
  "pdf-press": TEXT.pdfPress.title,
  "media-mill": TEXT.mediaMill.title,
};

/** Os ids do registro; a ordem de exibição é a do `RECIPE_LIST`, não esta. */
// O filtro nunca descarta nada: só estreita `string` para `RecipeId` sem um `as`.
export const RECIPE_IDS: readonly RecipeId[] = Object.keys(RECIPE_TITLES).filter(isRecipeId);

function isRecipeId(candidate: string): candidate is RecipeId {
  return candidate in RECIPE_TITLES;
}
