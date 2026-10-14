import { z } from "zod";

import type { ApiClient } from "../api/client";
import { runSchema, type RunStatus } from "../runs/api";

// Espelho em runtime de GET /runs; api.test.ts trava a igualdade com o tipo gerado da spec.
const runListSchema = z.object({
  data: z.array(runSchema),
  next_cursor: z.string().nullable(),
});

export type RunList = z.infer<typeof runListSchema>;

/** Parâmetros de GET /runs que a interface usa; `limit` é sempre explícito. */
export interface RunListQuery {
  readonly limit: number;
  readonly recipeId?: string;
  readonly status?: RunStatus;
  /** Instante ISO 8601, inclusivo. */
  readonly createdFrom?: string;
  /** Cursor opaco da página anterior (keyset); vale só para os mesmos filtros. */
  readonly cursor?: string;
}

/** Caminho com a query string, sempre na mesma ordem: o cache e os testes comparam por ele. */
export function runListPath(query: RunListQuery): string {
  const params = new URLSearchParams({ limit: String(query.limit) });
  const optional: readonly (readonly [string, string | undefined])[] = [
    ["recipe_id", query.recipeId],
    ["status", query.status],
    ["created_from", query.createdFrom],
    ["cursor", query.cursor],
  ];
  for (const [name, value] of optional) {
    if (value !== undefined) {
      params.set(name, value);
    }
  }
  return `/api/v1/runs?${params.toString()}`;
}

/** GET /runs: as execuções da conta, das mais novas para as mais antigas. */
export async function listRuns(
  client: ApiClient,
  query: RunListQuery,
  signal?: AbortSignal,
): Promise<RunList> {
  const response = await client.request({
    method: "GET",
    path: runListPath(query),
    schema: runListSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data;
}
