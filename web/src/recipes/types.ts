// Tipos de receita derivados do corpo de POST /runs gerado da spec: uma receita nova entra
// regenerando os tipos, e o compilador aponta o registro (registry.ts) que precisa do formulário dela.
import type { RunCommand } from "../api/types";
import type { FormIssue } from "../components/a11y/ErrorSummary";

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

export type RecipeId = RunCommand["recipe_id"];

/** Receita, versão e parâmetros: o que o formulário da receita produz, sem as entradas. */
export type RecipeCommand = DistributiveOmit<RunCommand, "input_upload_ids">;

export type RecipeCommandFor<K extends RecipeId> = Extract<RecipeCommand, { recipe_id: K }>;

export type RecipeParameters<K extends RecipeId> = RecipeCommandFor<K>["parameters"];

/** Estado atual do formulário de uma receita: o comando válido, ou os erros por campo. */
export type RecipeDraft<K extends RecipeId> =
  | { readonly ok: true; readonly command: RecipeCommandFor<K> }
  | { readonly ok: false; readonly issues: readonly FormIssue[] };

/** O que a Bancada passa ao formulário de qualquer receita. */
export interface RecipeFormProps<K extends RecipeId> {
  /** Erro de cada controle, pelo id; já filtrado para mostrar só depois de uma tentativa de envio. */
  readonly errors: ReadonlyMap<string, string>;
  /** Chamado a cada mudança, com o comando válido ou os erros do formulário inteiro. */
  readonly onDraftChange: (draft: RecipeDraft<K>) => void;
}

/** Props do formulário de uma receita: as da Bancada mais os defaults e limites do catálogo. */
export interface RecipeFieldsProps<K extends RecipeId, S> extends RecipeFormProps<K> {
  readonly settings: S;
}
