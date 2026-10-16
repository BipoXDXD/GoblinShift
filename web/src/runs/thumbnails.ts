import type { ApiClient } from "../api/client";
import { ApiError } from "../api/response";
import { artifactDownloadPath } from "./commands";

/**
 * Miniaturas do Media Mill como preview na tela do resultado.
 *
 * Os downloads continuam por navegação (`<a href>`, 2.2): um arquivo de até 512 MiB nunca passa
 * pela memória da página. A miniatura é a exceção, e só ela: um JPEG de até 640 px que o próprio
 * serviço derivou do vídeo. Para virar `<img>` ela precisa chegar como Blob, porque o download
 * responde `attachment` com `octet-stream` e `CSP: sandbox` (3.6). Por isso o pedido tem teto de
 * bytes, confere a assinatura do JPEG e cria o Blob com tipo fixo (ApiClient.requestJpeg). O
 * vídeo enviado nunca é mostrado: nem `<video>`, nem URL do arquivo escolhido.
 */
const THUMBNAIL_MAX_BYTES = 1024 * 1024;
/** Até 5 vídeos com 3 miniaturas cada (1.5); mais que isso não é Media Mill. */
const MAX_PREVIEWS = 15;
/** Tentativas depois de um 429: o servidor limita 2 downloads simultâneos por conta (1.7). */
const MAX_RATE_LIMIT_RETRIES = 2;
const DEFAULT_RETRY_AFTER_MS = 2_000;
const MAX_WAIT_MS = 30_000;
const TOO_MANY_REQUESTS = 429;
const THUMBNAILS_PREFIX = "thumbnails/";
const JPEG_SUFFIX = ".jpg";

export interface ThumbnailArtifact {
  readonly artifact_id: string;
  readonly role: string;
  readonly file_name: string;
}

export type PreviewState =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly url: string }
  | { readonly status: "failed" };

/** Saídas que são miniaturas JPEG do Media Mill, na ordem publicada, até o teto de previews. */
export function thumbnailArtifacts<A extends ThumbnailArtifact>(artifacts: readonly A[]): A[] {
  return artifacts
    .filter(
      (artifact) =>
        artifact.role === "output" &&
        artifact.file_name.startsWith(THUMBNAILS_PREFIX) &&
        artifact.file_name.toLowerCase().endsWith(JPEG_SUFFIX),
    )
    .slice(0, MAX_PREVIEWS);
}

export interface PreviewServices {
  readonly fetchJpeg: (artifactId: string, signal: AbortSignal) => Promise<Blob>;
  readonly createUrl: (blob: Blob) => string;
  readonly wait: (ms: number, signal: AbortSignal) => Promise<void>;
}

export function browserPreviewServices(client: ApiClient): PreviewServices {
  return {
    fetchJpeg: (artifactId, signal) =>
      client.requestJpeg({
        path: artifactDownloadPath(artifactId),
        maxBytes: THUMBNAIL_MAX_BYTES,
        signal,
      }),
    createUrl: (blob) => URL.createObjectURL(blob),
    wait: (ms, signal) =>
      new Promise<void>((resolve, reject) => {
        const timer = setTimeout(resolve, ms);
        signal.addEventListener(
          "abort",
          () => {
            clearTimeout(timer);
            reject(new Error("a busca da miniatura foi cancelada"));
          },
          { once: true },
        );
      }),
  };
}

/**
 * Busca as miniaturas uma de cada vez, na ordem, e informa cada desfecho. Um 429 espera o
 * Retry-After (com teto) e tenta de novo poucas vezes; outra falha marca só aquela miniatura.
 * Para quando `signal` aborta (a tela saiu), sem informar mais nada.
 */
export interface PreviewLoad {
  readonly artifactIds: readonly string[];
  readonly services: PreviewServices;
  readonly onResult: (artifactId: string, state: PreviewState) => void;
  readonly signal: AbortSignal;
}

export async function loadPreviews({
  artifactIds,
  services,
  onResult,
  signal,
}: PreviewLoad): Promise<void> {
  for (const artifactId of artifactIds) {
    const state = await loadOne(artifactId, services, signal);
    if (signal.aborted) {
      return;
    }
    onResult(artifactId, state);
  }
}

async function loadOne(
  artifactId: string,
  services: PreviewServices,
  signal: AbortSignal,
): Promise<PreviewState> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      const blob = await services.fetchJpeg(artifactId, signal);
      return { status: "ready", url: services.createUrl(blob) };
    } catch (error) {
      const waitMs = rateLimitWait(error);
      if (signal.aborted || waitMs === null || attempt >= MAX_RATE_LIMIT_RETRIES) {
        return { status: "failed" };
      }
      try {
        await services.wait(waitMs, signal);
      } catch {
        // A espera só termina com erro quando a tela saiu (abort).
        return { status: "failed" };
      }
    }
  }
}

function rateLimitWait(error: unknown): number | null {
  if (!(error instanceof ApiError) || error.failure.kind !== "problem") {
    return null;
  }
  const { status, retryAfterMs } = error.failure;
  if (status !== TOO_MANY_REQUESTS) {
    return null;
  }
  return Math.min(retryAfterMs ?? DEFAULT_RETRY_AFTER_MS, MAX_WAIT_MS);
}
