import type { ReceivedContent } from "../upload/api";
import type { ContentUploader, UploadContentOptions } from "../upload/xhr";

const SHA256 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";

interface PendingUpload {
  readonly options: UploadContentOptions;
  readonly resolve: () => void;
  readonly reject: (reason: Error) => void;
}

/**
 * Uploader falso. Com `autoResolve`, confirma cada envio na hora; sem ele, o envio fica pendente
 * até o teste chamar `resolve` ou `reject`, e o progresso sai por `options.onProgress`.
 */
export function fakeUploader({ autoResolve }: { autoResolve: boolean }): {
  readonly uploader: ContentUploader;
  readonly pending: readonly PendingUpload[];
} {
  const pending: PendingUpload[] = [];
  const uploader: ContentUploader = {
    upload: (options) =>
      new Promise<ReceivedContent>((resolve, reject) => {
        options.signal?.throwIfAborted();
        const received = receivedFor(options);
        if (autoResolve) {
          resolve(received);
          return;
        }
        options.signal?.addEventListener("abort", () => {
          reject(new DOMException("cancelado", "AbortError"));
        });
        pending.push({
          options,
          resolve: () => {
            resolve(received);
          },
          reject,
        });
      }),
  };
  return { uploader, pending };
}

function receivedFor({ content, file }: UploadContentOptions): ReceivedContent {
  const id = content.url.split("/")[4] ?? "";
  return { id, state: "ready", size: file.size, sha256: SHA256 };
}
