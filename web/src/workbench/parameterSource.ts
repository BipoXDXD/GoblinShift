import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { z } from "zod";

import { useApiClient } from "../api/ApiClientContext";
import { PRESET_PARAM, RUN_PARAM } from "../app/paths";
import { formatDateTime } from "../format";
import { getPreset, PRESETS_QUERY_KEY, type Preset } from "../presets/api";
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
  readonly stored: StoredParameters;
  readonly loadedNotice: string;
  readonly wrongRecipeNotice: string;
  /** O preset lido, para "Atualizar preset" (id, revisão e nome); `null` na execução. */
  readonly preset: Preset | null;
}

/**
 * Os parâmetros guardados, ou o aviso de por que já não estão. Só a execução os perde (retenção de
 * 30 dias); o preset sempre os tem, então não carrega um aviso que nunca aparece.
 */
export type StoredParameters =
  | { readonly kind: "kept"; readonly parameters: Readonly<Record<string, unknown>> }
  | { readonly kind: "gone"; readonly notice: string };

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

/**
 * A origem não sobrevive à Bancada (`gcTime: 0`): cada abertura lê o preset ou a execução da API.
 * O formulário e o "Atualizar preset" só a leem na montagem, então uma origem em cache, vencida por
 * um `PUT` ou por outra aba, abriria com parâmetros e revisão velhos (e o próximo `PUT` daria 412).
 * Enquanto a Bancada está aberta, ela observa a consulta e a entrada fica.
 */
export function useParameterSource(ref: SourceRef | null): UseQueryResult<ParameterSource> {
  const client = useApiClient();
  return useQuery({
    queryKey: ref?.kind === "preset" ? presetSourceKey(ref.id) : ["runs", ref?.id, "source"],
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
    gcTime: 0,
  });
}

function presetSourceKey(id: string): readonly unknown[] {
  return [...PRESETS_QUERY_KEY, id];
}

function presetSource(preset: Preset): ParameterSource {
  return {
    recipeId: preset.recipe_id,
    recipeVersion: preset.recipe_version,
    stored: { kind: "kept", parameters: preset.parameters },
    loadedNotice: TEXT.presets.loaded(preset.name),
    wrongRecipeNotice: TEXT.presets.wrongRecipe,
    preset,
  };
}

function runSource(run: Awaited<ReturnType<typeof getRun>>["data"]): ParameterSource {
  return {
    recipeId: run.recipe_id,
    recipeVersion: run.recipe_version,
    stored:
      run.parameters === null
        ? { kind: "gone", notice: TEXT.history.reuseGone }
        : { kind: "kept", parameters: run.parameters },
    loadedNotice: TEXT.history.reuseLoaded(formatDateTime(run.created_at)),
    wrongRecipeNotice: TEXT.history.reuseWrongRecipe,
    preset: null,
  };
}
