export const PRESET_ID = "5e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a5b";

/** Preset no formato de GET /presets/{id}, com os campos a sobrescrever. */
export function presetBody(overrides: Record<string, unknown> = {}): {
  id: string;
  name: string;
  recipe_id: string;
  recipe_version: number;
  parameters: Record<string, unknown>;
  revision: number;
  created_at: string;
  updated_at: string;
} {
  return {
    id: PRESET_ID,
    name: "Clientes do mês",
    recipe_id: "csv-janitor",
    recipe_version: 1,
    parameters: {
      input_delimiter: ",",
      output_delimiter: ";",
      trim_columns: ["nome"],
      case_rules: [],
      filters: [],
      deduplicate_by: [],
      output_columns: null,
      formula_guard: "tab_prefix",
    },
    revision: 1,
    created_at: "2026-10-14T10:00:00Z",
    updated_at: "2026-10-14T10:00:00Z",
    ...overrides,
  };
}
