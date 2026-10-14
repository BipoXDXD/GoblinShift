import { RECIPE_LIST } from "../recipes/registry";
import type { RecipeId } from "../recipes/types";
import { RUN_STATUSES, type RunStatus } from "../runs/api";
import type { RunListQuery } from "./api";

export const PERIODS = ["7d", "30d", "90d"] as const;
export type Period = (typeof PERIODS)[number];

const PERIOD_DAYS: Readonly<Record<Period, number>> = { "7d": 7, "30d": 30, "90d": 90 };
const DAY_MS = 86_400_000;
/** Página do Histórico; o padrão da API, explícito para não depender dele. */
const HISTORY_PAGE_SIZE = 25;

/** Filtros do Histórico, já validados; `null` é "sem filtro". */
export interface HistoryFilters {
  readonly recipe: RecipeId | null;
  readonly status: RunStatus | null;
  readonly period: Period | null;
}

export const NO_FILTERS: HistoryFilters = { recipe: null, status: null, period: null };

// Nomes dos parâmetros na URL da tela, no idioma das URLs (paths.ts).
const PARAM = { recipe: "receita", status: "estado", period: "periodo" } as const;

const RECIPE_IDS: readonly RecipeId[] = RECIPE_LIST.map((recipe) => recipe.id);

/**
 * Filtros da query string da tela, por allowlist: valor fora da lista é ignorado e nunca chega à
 * API. A URL é entrada externa (pode ser colada ou forjada); limit e cursor nem são lidos.
 */
export function parseHistoryFilters(params: URLSearchParams): HistoryFilters {
  return {
    recipe: oneOf(params.get(PARAM.recipe), RECIPE_IDS),
    status: oneOf(params.get(PARAM.status), RUN_STATUSES),
    period: oneOf(params.get(PARAM.period), PERIODS),
  };
}

export function filtersToSearchParams(filters: HistoryFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of ["recipe", "status", "period"] as const) {
    const value = filters[key];
    if (value !== null) {
      params.set(PARAM[key], value);
    }
  }
  return params;
}

export function hasActiveFilters(filters: HistoryFilters): boolean {
  return filters.recipe !== null || filters.status !== null || filters.period !== null;
}

/**
 * Filtros → GET /runs. O período é uma janela móvel de N × 24 h até `anchorMs`, inclusiva no
 * início e aberta no fim. O cursor da API vale só para os mesmos filtros, então quem chama fixa
 * `anchorMs` enquanto a lista estiver aberta.
 */
export function toRunListQuery(filters: HistoryFilters, anchorMs: number): RunListQuery {
  return {
    limit: HISTORY_PAGE_SIZE,
    ...(filters.recipe === null ? {} : { recipeId: filters.recipe }),
    ...(filters.status === null ? {} : { status: filters.status }),
    ...(filters.period === null
      ? {}
      : {
          createdFrom: new Date(anchorMs - PERIOD_DAYS[filters.period] * DAY_MS).toISOString(),
        }),
  };
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | null {
  return allowed.find((candidate) => candidate === value) ?? null;
}
