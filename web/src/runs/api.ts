import { z } from "zod";

import type { ApiClient } from "../api/client";
import type { ApiResponse } from "../api/response";

// Espelhos em runtime dos schemas da spec; api.test.ts trava a igualdade com os tipos gerados.
export const RUN_STATUSES = [
  "queued",
  "running",
  "stopping",
  "succeeded",
  "succeeded_with_warnings",
  "canceled",
  "failed",
] as const;
const RUN_PHASES = ["inspecting", "processing", "packaging", "finalizing", "retry_wait"] as const;

export const runSchema = z.object({
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
  parameters: z.record(z.string(), z.unknown()).nullable(),
  preset_id: z.uuid().nullable(),
  results_expire_at: z.iso.datetime({ offset: true }).nullable(),
});

// Códigos fechados da spec; um código novo chega como desconhecido e a tela o trata assim.
const ITEM_STATUSES = ["pending", "ok", "rejected", "duplicate"] as const;
const ITEM_REJECTIONS = [
  "UNSUPPORTED_FORMAT",
  "UNSUPPORTED_BIT_DEPTH",
  "UNSUPPORTED_COLOR_PROFILE",
  "IMAGE_TOO_LARGE",
  "CSV_STRUCTURE_INVALID",
  "FIELD_LIMIT_EXCEEDED",
] as const;
const ITEM_WARNINGS = [
  "CSV_ALL_RECORDS_FILTERED",
  "CSV_FORMULA_CELLS",
  "CSV_FORMULA_HEADER",
] as const;

const runItemSchema = z.object({
  position: z.number().int(),
  upload_id: z.uuid(),
  status: z.enum(ITEM_STATUSES),
  error_code: z.enum(ITEM_REJECTIONS).nullable(),
  warnings: z.array(z.enum(ITEM_WARNINGS)),
  counts: z.record(z.string(), z.number().int()),
  duplicate_of: z.number().int().nullable(),
});

const runSummarySchema = z.object({
  inputs: z.number().int(),
  ok: z.number().int(),
  rejected: z.number().int(),
  duplicates: z.number().int(),
  outputs: z.number().int(),
});

/** GET /runs/{id}: a execução, o resumo depois da publicação e o desfecho de cada entrada. */
const runDetailSchema = runSchema.extend({
  summary: runSummarySchema.nullable(),
  items: z.array(runItemSchema),
});

export type Run = z.infer<typeof runSchema>;
export type RunDetail = z.infer<typeof runDetailSchema>;
export type RunItem = RunDetail["items"][number];
export type RunSummary = NonNullable<RunDetail["summary"]>;
export type RunStatus = Run["status"];
export type RunPhase = NonNullable<Run["progress"]["phase"]>;

/** GET /runs/{id}; o Retry-After da resposta dita a próxima consulta. */
export function getRun(
  client: ApiClient,
  runId: string,
  signal?: AbortSignal,
): Promise<ApiResponse<RunDetail>> {
  return client.request({
    method: "GET",
    path: `/api/v1/runs/${encodeURIComponent(runId)}`,
    schema: runDetailSchema,
    ...(signal === undefined ? {} : { signal }),
  });
}
