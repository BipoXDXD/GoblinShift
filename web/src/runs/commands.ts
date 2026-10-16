import { z } from "zod";

import type { ApiClient } from "../api/client";
import type { RunCommand } from "../api/types";
import { runSchema } from "./api";

const ACCEPTED = 202;

// Espelhos em runtime dos schemas da spec; commands.test.ts trava a igualdade com os tipos gerados.
const acceptedRunSchema = runSchema.extend({
  idempotency_expires_at: z.iso.datetime({ offset: true }),
});

const resultReferenceSchema = z.object({
  artifact_id: z.uuid(),
  logical_name: z.string(),
  role: z.enum(["output", "report", "manifest", "package"]),
  file_name: z.string(),
});

const runResultsSchema = z.object({
  published: z.boolean(),
  expires_at: z.iso.datetime({ offset: true }).nullable(),
  artifacts: z.array(resultReferenceSchema),
});

const artifactDetailsSchema = z.object({
  id: z.uuid(),
  role: z.enum(["output", "report", "manifest", "package"]),
  size: z.number().int(),
  sha256: z.string(),
  expires_at: z.iso.datetime({ offset: true }),
  availability: z.enum(["available", "expired", "deleted"]),
});

/** O 202 do DELETE chega sem corpo; só o status importa. */
const acceptedSchema = z.unknown();

// `status` é string na spec: o cancelamento devolve o estado real, e enum de saída pode crescer.
const cancelOutcomeSchema = z.object({
  id: z.uuid(),
  status: z.string(),
  error_code: z.string().nullable(),
});

export type AcceptedRun = z.infer<typeof acceptedRunSchema>;
export type ArtifactDetails = z.infer<typeof artifactDetailsSchema>;
export type CancelOutcome = z.infer<typeof cancelOutcomeSchema>;
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

/**
 * POST /runs/{id}/cancel: 202 quando o pedido foi registrado e a execução ainda vai parar; 200
 * quando ela já era terminal, com o estado real. A interface não presume nada: o polling mostra.
 */
export async function cancelRun(
  client: ApiClient,
  runId: string,
): Promise<{ readonly outcome: CancelOutcome; readonly accepted: boolean }> {
  const response = await client.request({
    method: "POST",
    path: `/api/v1/runs/${encodeURIComponent(runId)}/cancel`,
    schema: cancelOutcomeSchema,
  });
  return { outcome: response.data, accepted: response.status === ACCEPTED };
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

/** GET /artifacts/{id}: metadados e disponibilidade (vencido ou excluído) de um artefato. */
export async function getArtifact(
  client: ApiClient,
  artifactId: string,
  signal?: AbortSignal,
): Promise<ArtifactDetails> {
  const response = await client.request({
    method: "GET",
    path: `/api/v1/artifacts/${encodeURIComponent(artifactId)}`,
    schema: artifactDetailsSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data;
}

/**
 * DELETE /runs/{id}/artifacts: exclui as saídas de uma execução terminal antes das 48 h (202),
 * sem tocar nos originais. Execução que ainda não terminou responde 409.
 */
export async function deleteRunArtifacts(client: ApiClient, runId: string): Promise<void> {
  await client.request({
    method: "DELETE",
    path: `/api/v1/runs/${encodeURIComponent(runId)}/artifacts`,
    schema: acceptedSchema,
  });
}

/**
 * Rota de download de um artefato. O download é sempre por navegação (`<a href>`), com o
 * `attachment` do servidor, nunca por fetch para um Blob, que traria até 512 MiB à memória (2.2).
 */
export function artifactDownloadPath(artifactId: string): string {
  return `/api/v1/artifacts/${encodeURIComponent(artifactId)}/download`;
}
