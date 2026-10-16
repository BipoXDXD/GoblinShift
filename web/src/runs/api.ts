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

// Códigos fechados da spec; api.test.ts trava as listas com os tipos gerados. Um código que o
// servidor passe a mandar antes de a interface conhecê-lo vira UNKNOWN_CODE (com o original
// guardado para a tela), em vez de derrubar o parse da execução inteira.
export const ITEM_STATUSES = ["pending", "ok", "rejected", "duplicate"] as const;
export const ITEM_REJECTIONS = [
  "UNSUPPORTED_FORMAT",
  "UNSUPPORTED_BIT_DEPTH",
  "UNSUPPORTED_COLOR_PROFILE",
  "IMAGE_TOO_LARGE",
  "CSV_STRUCTURE_INVALID",
  "FIELD_LIMIT_EXCEEDED",
] as const;
export const ITEM_WARNINGS = [
  "CSV_ALL_RECORDS_FILTERED",
  "CSV_FORMULA_CELLS",
  "CSV_FORMULA_HEADER",
  "INPUT_NOT_SELECTED",
] as const;
export const ITEM_NOTICES = ["IMAGE_P3_CONVERTED"] as const;

export const UNKNOWN_CODE = "unknown";

/** Código da lista fechada, ou `{ code: UNKNOWN_CODE, raw }` para um que a interface não conhece. */
export type Coded<T extends string> =
  T | { readonly code: typeof UNKNOWN_CODE; readonly raw: string };

function tolerantCode<const T extends readonly [string, ...string[]]>(known: T) {
  return z.string().transform((raw): Coded<T[number]> => {
    const match = known.find((code) => code === raw);
    return match ?? { code: UNKNOWN_CODE, raw };
  });
}

/** Contagens inteiras e não negativas; um valor fora disso fica de fora, sem derrubar o resto. */
const countsSchema = z
  .record(z.string(), z.unknown())
  .transform((counts) =>
    Object.fromEntries(
      Object.entries(counts).filter(
        (entry): entry is [string, number] => Number.isInteger(entry[1]) && Number(entry[1]) >= 0,
      ),
    ),
  );

const runItemSchema = z.object({
  /** Ordem de entrada a partir de 0, como na spec; a tela mostra a partir de 1. */
  position: z.number().int(),
  upload_id: z.uuid(),
  /** Nome do arquivo enviado; nulo depois de 30 dias. */
  original_name: z.string().nullable(),
  status: tolerantCode(ITEM_STATUSES),
  error_code: tolerantCode(ITEM_REJECTIONS).nullable(),
  warnings: z.array(tolerantCode(ITEM_WARNINGS)),
  /** Fatos sobre a entrada que não são aviso (não mudam o estado da execução). */
  notices: z.array(tolerantCode(ITEM_NOTICES)),
  counts: countsSchema,
  /** Posição (a partir de 0) da primeira entrada igual. */
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
