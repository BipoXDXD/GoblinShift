import { z } from "zod";

import type { ApiClient } from "../api/client";
import { ApiError } from "../api/response";
import type { RecipeCommand } from "../recipes/types";

// Espelho em runtime do PresetResponse da spec; api.test.ts trava a igualdade com os tipos gerados.
const presetSchema = z.object({
  id: z.uuid(),
  name: z.string(),
  recipe_id: z.string(),
  recipe_version: z.number().int(),
  parameters: z.record(z.string(), z.unknown()),
  revision: z.number().int(),
  created_at: z.iso.datetime({ offset: true }),
  updated_at: z.iso.datetime({ offset: true }),
});

const presetListSchema = z.object({ data: z.array(presetSchema) });

/** O 204 do DELETE chega sem corpo; só o status importa. */
const noContentSchema = z.unknown();
const NOT_FOUND = 404;

export type Preset = z.infer<typeof presetSchema>;
/** Corpo de POST /presets: o nome e o comando da receita, sem as entradas. */
export type PresetDraft = { name: string } & RecipeCommand;

/**
 * Corpo de PUT /presets/{id}: o `PresetDraft` da Bancada, ou o comando que o próprio preset guarda
 * (Renomear), que pode ser de uma receita que a interface não conhece. O servidor valida os dois.
 */
export type PresetReplacement = Pick<
  Preset,
  "name" | "recipe_id" | "recipe_version" | "parameters"
>;

export const PRESETS_QUERY_KEY = ["presets"] as const;

/** GET /presets: os presets da conta, até 100, já ordenados pelo nome. */
export async function listPresets(client: ApiClient, signal?: AbortSignal): Promise<Preset[]> {
  const response = await client.request({
    method: "GET",
    path: "/api/v1/presets",
    schema: presetListSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data.data;
}

export async function getPreset(
  client: ApiClient,
  presetId: string,
  signal?: AbortSignal,
): Promise<Preset> {
  const response = await client.request({
    method: "GET",
    path: `/api/v1/presets/${encodeURIComponent(presetId)}`,
    schema: presetSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data;
}

export async function createPreset(client: ApiClient, draft: PresetDraft): Promise<Preset> {
  const response = await client.request({
    method: "POST",
    path: "/api/v1/presets",
    body: draft,
    schema: presetSchema,
  });
  return response.data;
}

/**
 * PUT /presets/{id}: troca nome e parâmetros com o `If-Match` da revisão lida. Revisão vencida
 * volta como 412 e nome repetido como 409. O 404 sobe como falha: renomear o que sumiu não é
 * sucesso.
 */
export async function replacePreset(
  client: ApiClient,
  preset: Preset,
  draft: PresetReplacement,
): Promise<Preset> {
  const response = await client.request({
    method: "PUT",
    path: `/api/v1/presets/${encodeURIComponent(preset.id)}`,
    ifMatch: etagOf(preset),
    body: draft,
    schema: presetSchema,
  });
  return response.data;
}

/**
 * DELETE /presets/{id} com `If-Match` derivado de `revision` (ETag documentado: a revisão entre
 * aspas; 2.2), porque o cache do TanStack guarda só o corpo. Revisão vencida volta como 412;
 * preset que já não existe conta como excluído.
 */
export async function deletePreset(client: ApiClient, preset: Preset): Promise<void> {
  try {
    await client.request({
      method: "DELETE",
      path: `/api/v1/presets/${encodeURIComponent(preset.id)}`,
      ifMatch: etagOf(preset),
      schema: noContentSchema,
    });
  } catch (error: unknown) {
    if (!isNotFound(error)) {
      throw error;
    }
  }
}

/** Revisão vencida (412): o preset mudou em outra aba ou dispositivo depois da leitura. */
export function isStaleRevision(error: Error): boolean {
  return (
    error instanceof ApiError &&
    error.failure.kind === "problem" &&
    error.failure.problem.code === "PRECONDITION_FAILED"
  );
}

/** Preset que já não existe (404): excluído em outra aba ou dispositivo. */
export function isNotFound(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.failure.kind === "problem" &&
    error.failure.status === NOT_FOUND
  );
}

function etagOf(preset: Preset): string {
  return `"${String(preset.revision)}"`;
}
