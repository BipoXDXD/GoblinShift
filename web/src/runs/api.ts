import { z } from "zod";

import type { ApiClient } from "../api/client";
import type { ApiResponse } from "../api/response";

// Espelhos em runtime dos schemas da spec; api.test.ts trava a igualdade com os tipos gerados.
const RUN_STATUSES = [
  "queued",
  "running",
  "stopping",
  "succeeded",
  "succeeded_with_warnings",
  "canceled",
  "failed",
] as const;
const RUN_PHASES = ["inspecting", "processing", "packaging", "finalizing", "retry_wait"] as const;

const runSchema = z.object({
  id: z.uuid(),
  recipe_id: z.string(),
  recipe_version: z.number().int(),
  status: z.enum(RUN_STATUSES),
  attempt: z.number().int(),
  progress: z.object({
    phase: z.enum(RUN_PHASES).nullable(),
    processed: z.number().int(),
    total: z.number().int(),
  }),
  queue_ahead: z.number().int(),
  created_at: z.iso.datetime({ offset: true }),
  queue_expires_at: z.iso.datetime({ offset: true }),
  deadline_at: z.iso.datetime({ offset: true }).nullable(),
  error: z.object({ code: z.string(), message: z.string() }).nullable(),
});

export type Run = z.infer<typeof runSchema>;
export type RunStatus = Run["status"];
export type RunPhase = NonNullable<Run["progress"]["phase"]>;

/** GET /runs/{id}; o Retry-After da resposta dita a próxima consulta. */
export function getRun(
  client: ApiClient,
  runId: string,
  signal?: AbortSignal,
): Promise<ApiResponse<Run>> {
  return client.request({
    method: "GET",
    path: `/api/v1/runs/${encodeURIComponent(runId)}`,
    schema: runSchema,
    ...(signal === undefined ? {} : { signal }),
  });
}
