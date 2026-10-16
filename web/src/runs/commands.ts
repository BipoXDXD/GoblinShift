import { z } from "zod";

import type { ApiClient } from "../api/client";
import type { RunCommand } from "../api/types";
import { runSchema } from "./api";

// Espelhos em runtime dos schemas da spec; commands.test.ts trava a igualdade com os tipos gerados.
const acceptedRunSchema = runSchema.extend({
  idempotency_expires_at: z.iso.datetime({ offset: true }),
});

const resultReferenceSchema = z.object({
  artifact_id: z.uuid(),
  logical_name: z.string(),
  role: z.enum(["output", "report", "manifest", "package"]),
  file_name: z.string(),
  availability: z.enum(["available", "expired", "deleted"]),
});

const runResultsSchema = z.object({
  published: z.boolean(),
  expires_at: z.iso.datetime({ offset: true }).nullable(),
  artifacts: z.array(resultReferenceSchema),
});

export type AcceptedRun = z.infer<typeof acceptedRunSchema>;
export type RunResults = z.infer<typeof runResultsSchema>;

interface CreateRunRequest {
  readonly command: RunCommand;
  /** Uma por intenção (useIdempotencyKey): retentativa do mesmo comando reusa a chave. */
  readonly idempotencyKey: string;
  readonly signal?: AbortSignal;
}

/** POST /runs: a API deduplica pela chave e devolve a execução aceita (202). */
export async function createRun(
  client: ApiClient,
  { command, idempotencyKey, signal }: CreateRunRequest,
): Promise<AcceptedRun> {
  const response = await client.request({
    method: "POST",
    path: "/api/v1/runs",
    body: command,
    idempotencyKey,
    schema: acceptedRunSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data;
}

/** GET /runs/{id}/results: os artefatos publicados de uma execução terminada. */
export async function getRunResults(
  client: ApiClient,
  runId: string,
  signal?: AbortSignal,
): Promise<RunResults> {
  const response = await client.request({
    method: "GET",
    path: `/api/v1/runs/${encodeURIComponent(runId)}/results`,
    schema: runResultsSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data;
}

/**
 * Rota de download de um artefato. O download é sempre por navegação (`<a href>`), com o
 * `attachment` do servidor, nunca por fetch para um Blob, que traria até 512 MiB à memória (2.2).
 */
export function artifactDownloadPath(artifactId: string): string {
  return `/api/v1/artifacts/${encodeURIComponent(artifactId)}/download`;
}
