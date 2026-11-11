import { z } from "zod";

import { formIssue, type FormField } from "../../components/a11y/ErrorSummary";
import { TEXT } from "../../text";
import type { RecipeDraft, RecipeParameters } from "../types";

// Formulário da variante csv-janitor v1. Tipos e opções vêm do código gerado da spec; defaults e
// limites, que o tipo não carrega, vêm de GET /catalog em tempo de execução (settings.ts).
type Parameters = RecipeParameters<"csv-janitor">;
type Delimiter = NonNullable<Parameters["input_delimiter"]>;
type FormulaGuard = NonNullable<Parameters["formula_guard"]>;
type CaseRule = NonNullable<Parameters["case_rules"]>[number];
type Filter = NonNullable<Parameters["filters"]>[number];

export const DELIMITERS = [",", ";", "\t"] as const satisfies readonly Delimiter[];
export const FORMULA_GUARDS = ["none", "tab_prefix"] as const satisfies readonly FormulaGuard[];
export const CASE_MODES = ["lower", "upper"] as const satisfies readonly CaseRule["mode"][];
export const FILTER_OPS = [
  "eq",
  "ne",
  "contains",
  "is_empty",
] as const satisfies readonly Filter["op"][];
export interface CsvJanitorLimits {
  /** Máximo de colunas em cada lista e de regras de caixa (as colunas de uma tabela, 1.7). */
  readonly maxColumns: number;
  readonly maxFilters: number;
}

export interface CaseRuleRow {
  /** Identifica a linha na interface; não vai para a API. */
  readonly key: string;
  readonly column: string;
  readonly mode: CaseRule["mode"];
}

export interface FilterRow {
  readonly key: string;
  readonly column: string;
  readonly op: Filter["op"];
  /** Ignorado em `is_empty`. */
  readonly value: string;
}

/** Valores do formulário com os nomes da API; listas de colunas são texto, uma por linha. */
export interface CsvJanitorFormValues {
  readonly input_delimiter: Delimiter;
  readonly output_delimiter: Delimiter;
  readonly trim_columns: string;
  readonly case_rules: readonly CaseRuleRow[];
  readonly filters: readonly FilterRow[];
  readonly deduplicate_by: string;
  readonly output_columns: string;
  readonly formula_guard: FormulaGuard;
}

const ERRORS = TEXT.csvJanitor.errors;

const option = <T extends string>(values: readonly [T, ...T[]]) =>
  z.enum(values, { error: TEXT.recipeFields.invalidOption });

const columnName = z.string().min(1, { error: ERRORS.columnRequired });

const columnList = (maxColumns: number) =>
  z
    .string()
    .transform(splitColumnLines)
    .pipe(z.array(z.string()).max(maxColumns, { error: ERRORS.tooManyColumns(maxColumns) }));

const caseRule = z
  .object({ column: columnName, mode: option(CASE_MODES) })
  .transform(({ column, mode }): CaseRule => ({ column, mode }));

const filter = z
  .object({ column: columnName, op: option(FILTER_OPS), value: z.string() })
  .transform(({ column, op, value }): Filter => ({
    column,
    op,
    // A API recusa valor em is_empty e exige um (mesmo vazio) nos outros operadores.
    value: op === "is_empty" ? null : value,
  }));

function parametersSchema({
  maxColumns,
  maxFilters,
}: CsvJanitorLimits): z.ZodType<Required<Parameters>> {
  return z.object({
    input_delimiter: option(DELIMITERS),
    output_delimiter: option(DELIMITERS),
    trim_columns: columnList(maxColumns),
    case_rules: z.array(caseRule).max(maxColumns, { error: ERRORS.tooManyRows(maxColumns) }),
    filters: z.array(filter).max(maxFilters, { error: ERRORS.tooManyRows(maxFilters) }),
    deduplicate_by: columnList(maxColumns),
    output_columns: columnList(maxColumns).transform((columns) =>
      columns.length === 0 ? null : columns,
    ),
    formula_guard: option(FORMULA_GUARDS),
  });
}

/**
 * Valida o formulário e monta o comando com todos os parâmetros explícitos (1.3), ou devolve os
 * erros já apontando o id de cada campo.
 */
export function parseCsvJanitor(
  values: CsvJanitorFormValues,
  limits: CsvJanitorLimits,
): RecipeDraft<"csv-janitor"> {
  const result = parametersSchema(limits).safeParse(values);
  if (!result.success) {
    return {
      ok: false,
      issues: result.error.issues.map((issue) =>
        formIssue(csvJanitorField(issue.path.map(String)), issue.message),
      ),
    };
  }
  return {
    ok: true,
    command: { recipe_id: "csv-janitor", recipe_version: 1, parameters: result.data },
  };
}

const ID_PREFIX = "csv-janitor";
const T = TEXT.csvJanitor;
type SingleField = Exclude<keyof CsvJanitorFormValues, "case_rules" | "filters">;
type RowPart = "column" | "mode" | "op" | "value";

// Os mesmos rótulos do formulário (CsvJanitorFields): o resumo de erros cita o campo por eles.
const SINGLE_LABELS: Readonly<Record<SingleField, string>> = {
  input_delimiter: T.inputDelimiter,
  output_delimiter: T.outputDelimiter,
  trim_columns: T.trimColumns,
  deduplicate_by: T.deduplicateBy,
  output_columns: T.outputColumns,
  formula_guard: T.formulaGuard,
};
const ROW_PART_LABELS: Readonly<Record<RowPart, (row: number) => string>> = {
  column: T.column,
  mode: T.mode,
  op: T.op,
  value: T.value,
};

interface RowList {
  readonly label: string;
  /** A primeira parte recebe o erro da linha inteira. */
  readonly parts: readonly [RowPart, ...RowPart[]];
}

const ROW_LISTS: Readonly<Record<string, RowList>> = {
  case_rules: { label: T.caseRules, parts: ["column", "mode"] },
  // Erro da linha inteira (por exemplo, valor incompatível com a condição) vai para a condição.
  filters: { label: T.filters, parts: ["op", "column", "value"] },
};
const INDEX = /^\d+$/;

/**
 * Controle para um caminho de erro, relativo a `parameters` (do Zod ou do `errors[].field` do
 * servidor); `null` quando o caminho não é de um campo deste formulário. Nas linhas, o rótulo
 * leva o nome da lista: "Coluna (linha 1)" existe nas regras de caixa e nos filtros.
 */
export function csvJanitorField(path: readonly (string | number)[]): FormField | null {
  const [field, index, part] = path.map(String);
  if (field === undefined) {
    return null;
  }
  if (isSingleField(field)) {
    return { fieldId: singleFieldId(field), label: SINGLE_LABELS[field] };
  }
  const list = Object.hasOwn(ROW_LISTS, field) ? ROW_LISTS[field] : undefined;
  if (list === undefined) {
    return null;
  }
  if (index === undefined || !INDEX.test(index)) {
    return { fieldId: addButtonId(field), label: list.label };
  }
  const target = list.parts.find((candidate) => candidate === part) ?? list.parts[0];
  const row = Number(index);
  return {
    fieldId: rowFieldId(field, row, target),
    label: T.inRow(list.label, ROW_PART_LABELS[target](row + 1)),
  };
}

function isSingleField(field: string): field is SingleField {
  return Object.hasOwn(SINGLE_LABELS, field);
}

export function addButtonId(listField: string): string {
  return `${ID_PREFIX}-${listField}-adicionar`;
}

export function rowFieldId(listField: string, index: number, part: string): string {
  return `${ID_PREFIX}-${listField}-${String(index)}-${part}`;
}

export function singleFieldId(field: keyof CsvJanitorFormValues): string {
  return `${ID_PREFIX}-${field}`;
}

function splitColumnLines(text: string): string[] {
  // Só descarta linhas em branco: espaços num nome de coluna fazem parte dele.
  return text
    .split("\n")
    .map((line) => line.replace(/\r$/, ""))
    .filter((line) => line.trim() !== "");
}
