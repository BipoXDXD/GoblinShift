import { describe, expect, it } from "vitest";

import {
  filtersToSearchParams,
  hasActiveFilters,
  NO_FILTERS,
  parseHistoryFilters,
  toRunListQuery,
  type HistoryFilters,
} from "./filters";

const NOW = Date.parse("2026-10-14T12:00:00.000Z");
const DAY_MS = 86_400_000;

function parse(search: string): HistoryFilters {
  return parseHistoryFilters(new URLSearchParams(search));
}

describe("parseHistoryFilters (query string da tela, lida por allowlist)", () => {
  it("sem parâmetros, nenhum filtro", () => {
    expect(parse("")).toEqual(NO_FILTERS);
  });

  it("lê receita, estado e período válidos", () => {
    expect(parse("receita=image-forge&estado=failed&periodo=30d")).toEqual({
      recipe: "image-forge",
      status: "failed",
      period: "30d",
    });
  });

  it.each([
    ["estado=hacked", "estado fora da lista"],
    ["receita=..%2Fadmin", "receita fora do registro"],
    ["periodo=365d", "período fora da lista"],
    ["receita=&estado=&periodo=", "valores vazios"],
    ["receita=CSV-JANITOR", "caixa diferente"],
  ])("ignora valor inválido (%s: %s)", (search) => {
    expect(parse(search)).toEqual(NO_FILTERS);
  });

  it("ignora parâmetros que a tela não conhece, como limit e cursor", () => {
    expect(parse("limit=1000&cursor=abc&estado=queued")).toEqual({
      ...NO_FILTERS,
      status: "queued",
    });
  });

  it("com o parâmetro repetido, vale o primeiro", () => {
    expect(parse("estado=failed&estado=queued").status).toBe("failed");
  });
});

describe("filtersToSearchParams", () => {
  it("nenhum filtro, query string vazia", () => {
    expect(filtersToSearchParams(NO_FILTERS).toString()).toBe("");
  });

  it("vai e volta sem perder nada", () => {
    const filters: HistoryFilters = { recipe: "csv-janitor", status: "succeeded", period: "7d" };
    expect(parse(filtersToSearchParams(filters).toString())).toEqual(filters);
  });
});

describe("hasActiveFilters", () => {
  it.each<[HistoryFilters, boolean]>([
    [NO_FILTERS, false],
    [{ ...NO_FILTERS, recipe: "csv-janitor" }, true],
    [{ ...NO_FILTERS, status: "failed" }, true],
    [{ ...NO_FILTERS, period: "90d" }, true],
  ])("%o → %s", (filters, active) => {
    expect(hasActiveFilters(filters)).toBe(active);
  });
});

describe("toRunListQuery (filtros → GET /runs)", () => {
  it("sem filtros, só o tamanho da página", () => {
    expect(toRunListQuery(NO_FILTERS, NOW)).toEqual({ limit: 25 });
  });

  it("cada filtro vira o parâmetro da API", () => {
    expect(
      toRunListQuery({ recipe: "downloads-detox", status: "canceled", period: null }, NOW),
    ).toEqual({
      limit: 25,
      recipeId: "downloads-detox",
      status: "canceled",
    });
  });

  it.each([
    ["7d", "2026-10-07T12:00:00.000Z"],
    ["30d", "2026-09-14T12:00:00.000Z"],
    ["90d", "2026-07-16T12:00:00.000Z"],
  ] as const)("período %s começa exatamente N × 24 h antes de agora, inclusivo", (period, from) => {
    expect(toRunListQuery({ ...NO_FILTERS, period }, NOW)).toEqual({
      limit: 25,
      createdFrom: from,
    });
  });

  it("o período não tem fim: a execução criada agora entra", () => {
    const query = toRunListQuery({ ...NO_FILTERS, period: "7d" }, NOW);
    expect(query).not.toHaveProperty("createdTo");
    expect(Date.parse(query.createdFrom ?? "")).toBe(NOW - 7 * DAY_MS);
  });
});
