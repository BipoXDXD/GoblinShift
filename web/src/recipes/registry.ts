import type { ReactElement } from "react";

import { TEXT } from "../text";
import { CsvJanitorFields } from "./csv-janitor/CsvJanitorFields";
import { csvJanitorFieldId, CSV_JANITOR_DEFAULTS, parseCsvJanitor } from "./csv-janitor/schema";
import type { RecipeDraft, RecipeFieldsProps, RecipeId } from "./types";

/** O que a Bancada precisa de cada receita: o formulário escrito à mão e como apontar seus erros. */
export interface RecipeModule<K extends RecipeId> {
  readonly id: K;
  readonly title: string;
  /** Atributo `accept` do input de arquivos. */
  readonly accept: string;
  readonly initialDraft: RecipeDraft<K>;
  readonly Fields: (props: RecipeFieldsProps<K>) => ReactElement;
  /** Id do controle para um caminho relativo a `parameters`; `null` se não for um campo. */
  readonly fieldIdFor: (path: readonly string[]) => string | null;
}

// Um formulário por recipe_id da spec: uma receita nova na API (fatia 5) quebra o build aqui até
// ganhar o dela.
const RECIPES: { readonly [K in RecipeId]: RecipeModule<K> } = {
  "csv-janitor": {
    id: "csv-janitor",
    title: TEXT.csvJanitor.title,
    accept: ".csv,text/csv",
    initialDraft: parseCsvJanitor(CSV_JANITOR_DEFAULTS),
    Fields: CsvJanitorFields,
    fieldIdFor: csvJanitorFieldId,
  },
};

const BY_ID: ReadonlyMap<string, RecipeModule<RecipeId>> = new Map(Object.entries(RECIPES));

export function findRecipe(id: string): RecipeModule<RecipeId> | null {
  return BY_ID.get(id) ?? null;
}
