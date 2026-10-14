import { CSRF_HEADER, type CsrfTokenSource } from "../api/csrf";
import { API_ACCEPT, ApiError, interpretResponse } from "../api/response";
import { receivedContentSchema, type ContentInstructions, type ReceivedContent } from "./api";

/** A parte do XMLHttpRequest que o upload usa; nos testes, um objeto falso. */
export interface UploadXhr {
  open(method: string, url: string): void;
  setRequestHeader(name: string, value: string): void;
  send(body: Blob): void;
  abort(): void;
  getResponseHeader(name: string): string | null;
  readonly status: number;
  readonly responseText: string;
  readonly upload: { onprogress: ((event: ProgressEvent) => void) | null };
  onload: ((event: ProgressEvent) => void) | null;
  onerror: ((event: ProgressEvent) => void) | null;
  onabort: ((event: ProgressEvent) => void) | null;
}

/** Progresso real do envio: bytes saindo, depois a espera pela confirmação do servidor. */
export type UploadProgress =
  { phase: "sending"; loadedBytes: number; totalBytes: number } | { phase: "confirming" };

export interface UploadTransport {
  readonly createXhr: () => UploadXhr;
  readonly csrfToken: CsrfTokenSource;
  readonly now: () => number;
}

export interface UploadContentOptions {
  readonly content: ContentInstructions;
  readonly file: Blob;
  readonly onProgress: (progress: UploadProgress) => void;
  readonly signal?: AbortSignal;
}

export interface ContentUploader {
  upload(options: UploadContentOptions): Promise<ReceivedContent>;
}

/**
 * Envia os bytes de um upload por XMLHttpRequest, o único jeito no navegador de ter progresso do
 * envio (`upload.onprogress`); o fetch não informa bytes enviados.
 *
 * Usa `content.method`, `content.url` e `content.headers` exatamente como a API devolveu. Sem
 * retomada: falha aqui exige uma intenção nova (POST /uploads).
 */
export function createContentUploader(transport: UploadTransport): ContentUploader {
  return {
    upload: (options) => sendContent(transport, options),
  };
}

function sendContent(
  transport: UploadTransport,
  { content, file, onProgress, signal }: UploadContentOptions,
): Promise<ReceivedContent> {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const xhr = transport.createXhr();
    const abort = (): void => {
      xhr.abort();
    };
    const settle = (): void => {
      signal?.removeEventListener("abort", abort);
    };
    xhr.open(content.method, content.url);
    for (const [name, value] of Object.entries(content.headers)) {
      xhr.setRequestHeader(name, value);
    }
    xhr.setRequestHeader("Accept", API_ACCEPT);
    const token = transport.csrfToken();
    if (token !== null) {
      xhr.setRequestHeader(CSRF_HEADER, token);
    }
    xhr.upload.onprogress = (event) => {
      onProgress(progressOf(event.loaded, file.size));
    };
    xhr.onload = () => {
      settle();
      try {
        const raw = {
          status: xhr.status,
          header: (name: string) => xhr.getResponseHeader(name),
          text: xhr.responseText,
        };
        resolve(interpretResponse(raw, receivedContentSchema, transport.now()).data);
      } catch (error: unknown) {
        reject(asError(error));
      }
    };
    xhr.onerror = () => {
      settle();
      reject(new ApiError({ kind: "network" }));
    };
    xhr.onabort = () => {
      settle();
      reject(abortReason(signal));
    };
    signal?.addEventListener("abort", abort, { once: true });
    xhr.send(file);
  });
}

function progressOf(loadedBytes: number, totalBytes: number): UploadProgress {
  return loadedBytes >= totalBytes
    ? { phase: "confirming" }
    : { phase: "sending", loadedBytes, totalBytes };
}

function abortReason(signal: AbortSignal | undefined): Error {
  try {
    signal?.throwIfAborted();
  } catch (reason: unknown) {
    return asError(reason);
  }
  // Abortado sem sinal de quem chamou: o navegador cancelou (por exemplo, navegação).
  return new DOMException("envio cancelado", "AbortError");
}

function asError(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}
