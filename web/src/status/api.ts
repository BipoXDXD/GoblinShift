import { z } from "zod";

import type { ApiClient } from "../api/client";

// Espelho em runtime do ServiceStatusResponse da spec; api.test.ts trava a igualdade com o tipo
// gerado. Só níveis e a hora da leitura: a rota é pública e não traz contagem nenhuma (D9).
const PROCESSING_LEVELS = ["normal", "slow", "stalled"] as const;
const QUEUE_LEVELS = ["free", "busy", "full"] as const;

const serviceStatusSchema = z.object({
  api: z.literal("up"),
  processing: z.enum(PROCESSING_LEVELS),
  queue: z.enum(QUEUE_LEVELS),
  checked_at: z.iso.datetime({ offset: true }),
});

export type ServiceStatus = z.infer<typeof serviceStatusSchema>;
export type ProcessingLevel = ServiceStatus["processing"];
export type QueueLevel = ServiceStatus["queue"];

export const SERVICE_STATUS_QUERY_KEY = ["service-status"] as const;

/** GET /status: sem credencial; com o banco fora, 503 SERVICE_UNAVAILABLE. */
export async function getServiceStatus(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<ServiceStatus> {
  const response = await client.request({
    method: "GET",
    path: "/api/v1/status",
    schema: serviceStatusSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data;
}
