import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { z } from "zod";

import { useApiClient } from "../api/ApiClientContext";
import { PRESET_PARAM, RUN_PARAM } from "../app/paths";
import { formatDateTime } from "../format";
import { getPreset, PRESETS_QUERY_KEY } from "../presets/api";
import { getRun } from "../runs/api";
import { TEXT } from "../text";

/** De onde vêm os parâmetros iniciais da Bancada, pela URL: um preset ou uma execução. */
export interface SourceRef {
  readonly kind: "preset" | "run";
  readonly id: string;
}

/** Os parâmetros da origem, já com os textos do aviso que a Bancada mostra. */
export interface ParameterSource {
  readonly recipeId: string;
  readonly recipeVersion: number;
  /** `null` quando a origem já não guarda os parâmetros (retenção de 30 dias da execução). */
  readonly parameters: Readonly<Record<string, unknown>> | null;
  readonly loadedNotice: string;
  readonly goneNotice: string;
  readonly wrongRecipeNotice: string;
}

const idSchema = z.uuid();

/** Id fora do formato é ignorado: a Bancada abre com os padrões, sem consultar a API. */
export function sourceFromSearch(search: URLSearchParams): SourceRef | null {
  for (const [kind, param] of [
    ["preset", PRESET_PARAM],
    ["run", RUN_PARAM],
  ] as const) {
    const parsed = idSchema.safeParse(search.get(param));
    if (parsed.success) {
      return { kind, id: parsed.data };
    }
  }
  return null;
}

export function useParameterSource(ref: SourceRef | null): UseQueryResult<ParameterSource> {
  const client = useApiClient();
  return useQuery({
    queryKey: ref?.kind === "preset" ? [...PRESETS_QUERY_KEY, ref.id] : ["runs", ref?.id, "source"],
    queryFn: async ({ signal }): Promise<ParameterSource> => {
      if (ref === null) {
        throw new Error("origem ausente: a consulta só roda com uma");
      }
      return ref.kind === "preset"
        ? presetSource(await getPreset(client, ref.id, signal))
        : runSource((await getRun(client, ref.id, signal)).data);
    },
    enabled: ref !== null,
    retry: false,
  });
}

function presetSource(preset: Awaited<ReturnType<typeof getPreset>>): ParameterSource {
  return {
    recipeId: preset.recipe_id,
    recipeVersion: preset.recipe_version,
    parameters: preset.parameters,
    loadedNotice: TEXT.presets.loaded(preset.name),
    goneNotice: TEXT.presets.wrongRecipe,
    wrongRecipeNotice: TEXT.presets.wrongRecipe,
  };
}

function runSource(run: Awaited<ReturnType<typeof getRun>>["data"]): ParameterSource {
  return {
    recipeId: run.recipe_id,
    recipeVersion: run.recipe_version,
    parameters: run.parameters,
    loadedNotice: TEXT.history.reuseLoaded(formatDateTime(run.created_at)),
    goneNotice: TEXT.history.reuseGone,
    wrongRecipeNotice: TEXT.history.reuseWrongRecipe,
  };
}
