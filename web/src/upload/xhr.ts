import { CSRF_HEADER, type CsrfTokenSource } from "../api/csrf";
import { API_ACCEPT, ApiError, interpretResponse } from "../api/response";
import { sameOriginUrl } from "../api/sameOrigin";
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

// Sem progresso nem resposta por esse tempo, a conexão é dada como morta: sem isso o envio
// prenderia uma vaga do limitador e o aviso de saída da página para sempre.
const IDLE_TIMEOUT_MS = 45_000;

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
 * Usa `content.method`, `content.url` e `content.headers` como a API devolveu, mas recusa
 * (`CrossOriginUrlError`) uma URL fora da origem da interface, que levaria o CSRF junto. Sem
 * retomada: falha aqui exige uma intenção nova (POST /uploads). Aborta com
 * `ApiError({ kind: "timeout" })` depois de 45 s sem progresso nem resposta.
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
    const url = sameOriginUrl(content.url, window.location.origin);
    const xhr = transport.createXhr();
    const abort = (): void => {
      xhr.abort();
    };
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    let idleExpired = false;
    const rearmIdleTimer = (): void => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        idleExpired = true;
        xhr.abort();
      }, IDLE_TIMEOUT_MS);
    };
    const settle = (): void => {
      clearTimeout(idleTimer);
      signal?.removeEventListener("abort", abort);
    };
    xhr.open(content.method, url.href);
    for (const [name, value] of Object.entries(content.headers)) {
      xhr.setRequestHeader(name, value);
    }
    xhr.setRequestHeader("Accept", API_ACCEPT);
    const token = transport.csrfToken();
    if (token !== null) {
      xhr.setRequestHeader(CSRF_HEADER, token);
    }
    xhr.upload.onprogress = (event) => {
      rearmIdleTimer();
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
      reject(idleExpired ? new ApiError({ kind: "timeout" }) : abortReason(signal));
    };
    signal?.addEventListener("abort", abort, { once: true });
    xhr.send(file);
    rearmIdleTimer();
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
