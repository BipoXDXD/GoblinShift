import { z } from "zod";

import type { ApiClient } from "../api/client";

// Espelhos em runtime dos schemas da spec; api.test.ts trava a igualdade com os tipos gerados.
const UPLOAD_STATES = ["created", "receiving", "ready", "failed"] as const;

const contentInstructionsSchema = z.object({
  method: z.literal("PUT"),
  url: z.string(),
  headers: z.record(z.string(), z.string()),
});

const createdUploadSchema = z.object({
  id: z.uuid(),
  name: z.string(),
  size: z.number().int(),
  state: z.enum(UPLOAD_STATES),
  expires_at: z.iso.datetime({ offset: true }),
  content: contentInstructionsSchema,
});

const createdUploadsSchema = z.object({ items: z.array(createdUploadSchema) });

export const receivedContentSchema = z.object({
  id: z.uuid(),
  state: z.enum(UPLOAD_STATES),
  size: z.number().int(),
  sha256: z.string(),
});

export type ContentInstructions = z.infer<typeof contentInstructionsSchema>;
export type CreatedUpload = z.infer<typeof createdUploadSchema>;
export type ReceivedContent = z.infer<typeof receivedContentSchema>;

export interface UploadItem {
  readonly name: string;
  readonly size: number;
}

/** Cria as intenções de upload, uma por arquivo, na mesma ordem (POST /uploads). */
export async function createUploads(
  client: ApiClient,
  items: readonly UploadItem[],
  signal?: AbortSignal,
): Promise<readonly CreatedUpload[]> {
  const response = await client.request({
    method: "POST",
    path: "/api/v1/uploads",
    body: { items: items.map(({ name, size }) => ({ name, size })) },
    schema: createdUploadsSchema,
    ...(signal === undefined ? {} : { signal }),
  });
  return response.data.items;
}
