import { z } from "zod";

import type { FormIssue } from "../../components/a11y/ErrorSummary";
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
      issues: result.error.issues.map((issue): FormIssue => ({
        fieldId: csvJanitorFieldId(issue.path.map(String)),
        message: issue.message,
      })),
    };
  }
  return {
    ok: true,
    command: { recipe_id: "csv-janitor", recipe_version: 1, parameters: result.data },
  };
}

const ID_PREFIX = "csv-janitor";
const SINGLE_FIELDS: ReadonlySet<string> = new Set([
  "input_delimiter",
  "output_delimiter",
  "trim_columns",
  "deduplicate_by",
  "output_columns",
  "formula_guard",
]);
const ROW_FIELDS: Readonly<Record<string, readonly [string, ...string[]]>> = {
  case_rules: ["column", "mode"],
  // Erro da linha inteira (por exemplo, valor incompatível com a condição) vai para a condição.
  filters: ["op", "column", "value"],
};
const INDEX = /^\d+$/;

/**
 * Id do controle para um caminho de erro, relativo a `parameters` (do Zod ou do `errors[].field`
 * do servidor); `null` quando o caminho não é de um campo deste formulário.
 */
export function csvJanitorFieldId(path: readonly (string | number)[]): string | null {
  const [field, index, part] = path.map(String);
  if (field === undefined) {
    return null;
  }
  if (SINGLE_FIELDS.has(field)) {
    return `${ID_PREFIX}-${field}`;
  }
  const parts = Object.hasOwn(ROW_FIELDS, field) ? ROW_FIELDS[field] : undefined;
  if (parts === undefined) {
    return null;
  }
  if (index === undefined || !INDEX.test(index)) {
    return addButtonId(field);
  }
  const target = part !== undefined && parts.includes(part) ? part : parts[0];
  return rowFieldId(field, Number(index), target);
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
