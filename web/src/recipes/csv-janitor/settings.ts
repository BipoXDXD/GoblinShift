import { z } from "zod";

import type { CatalogVersion } from "../../catalog/api";
import {
  CASE_MODES,
  DELIMITERS,
  FILTER_OPS,
  FORMULA_GUARDS,
  type CsvJanitorFormValues,
  type CsvJanitorLimits,
} from "./schema";

/** Valores iniciais e limites do formulário, lidos da versão no catálogo (GET /catalog). */
export interface CsvJanitorSettings {
  readonly defaults: CsvJanitorFormValues;
  readonly limits: CsvJanitorLimits;
}

const columns = z.array(z.string());

const defaultsSchema = z.object({
  input_delimiter: z.enum(DELIMITERS),
  output_delimiter: z.enum(DELIMITERS),
  trim_columns: columns,
  case_rules: z.array(z.object({ column: z.string(), mode: z.enum(CASE_MODES) })),
  filters: z.array(
    z.object({ column: z.string(), op: z.enum(FILTER_OPS), value: z.string().nullable() }),
  ),
  deduplicate_by: columns,
  output_columns: columns.nullable(),
  formula_guard: z.enum(FORMULA_GUARDS),
});

// Uma configuração não referencia mais colunas do que uma tabela pode ter (1.7); o máximo de
// filtros só existe no schema dos parâmetros.
const limitsSchema = z.object({ columns: z.int().positive() });
const schemaBounds = z.object({
  properties: z.object({ filters: z.object({ maxItems: z.int().positive() }) }),
});

/** `null` quando o catálogo não traz a versão no formato que este formulário conhece. */
export function csvJanitorSettings(
  version: Pick<CatalogVersion, "defaults" | "limits" | "parameters_schema">,
): CsvJanitorSettings | null {
  const defaults = defaultsSchema.safeParse(version.defaults);
  const limits = limitsSchema.safeParse(version.limits);
  const bounds = schemaBounds.safeParse(version.parameters_schema);
  if (!defaults.success || !limits.success || !bounds.success) {
    return null;
  }
  return {
    defaults: formValuesOf(defaults.data),
    limits: {
      maxColumns: limits.data.columns,
      maxFilters: bounds.data.properties.filters.maxItems,
    },
  };
}

function formValuesOf(defaults: z.infer<typeof defaultsSchema>): CsvJanitorFormValues {
  return {
    input_delimiter: defaults.input_delimiter,
    output_delimiter: defaults.output_delimiter,
    trim_columns: defaults.trim_columns.join("\n"),
    case_rules: defaults.case_rules.map((rule, index) => ({
      key: `default-${String(index)}`,
      ...rule,
    })),
    filters: defaults.filters.map((filter, index) => ({
      key: `default-${String(index)}`,
      ...filter,
      value: filter.value ?? "",
    })),
    deduplicate_by: defaults.deduplicate_by.join("\n"),
    output_columns: (defaults.output_columns ?? []).join("\n"),
    formula_guard: defaults.formula_guard,
  };
}
