import type { UploadEntry, UploadEntryState } from "../upload/useUploadBatch";
import { TEXT } from "../text";

/** Máximo de entradas de POST /runs (`maxItems` de `input_upload_ids` na spec). */
export const MAX_RUN_INPUTS = 100;

type UploadStatus = UploadEntryState["status"];

const IN_PROGRESS: ReadonlySet<UploadStatus> = new Set(["waiting", "sending", "confirming"]);

/** Entradas na ordem escolhida; as que ainda não foram ordenadas vão para o fim, na ordem do lote. */
export function orderEntries(
  entries: readonly UploadEntry[],
  order: readonly string[],
): UploadEntry[] {
  const byId = new Map(entries.map((entry) => [entry.localId, entry]));
  const ordered = order.flatMap((localId) => byId.get(localId) ?? []);
  const placed = new Set(order);
  return [...ordered, ...entries.filter((entry) => !placed.has(entry.localId))];
}

export type RunInputs =
  | { readonly ok: true; readonly uploadIds: readonly string[] }
  | { readonly ok: false; readonly message: string };

/** Ids dos uploads prontos, na ordem da lista, ou o motivo de ainda não dar para executar. */
export function runInputs(entries: readonly UploadEntry[]): RunInputs {
  const kept = entries.filter((entry) => entry.state.status !== "canceled");
  if (kept.length === 0) {
    return { ok: false, message: TEXT.workbench.errors.noFiles };
  }
  if (kept.some((entry) => IN_PROGRESS.has(entry.state.status))) {
    return { ok: false, message: TEXT.workbench.errors.uploading };
  }
  if (kept.length > MAX_RUN_INPUTS) {
    return { ok: false, message: TEXT.workbench.errors.tooManyFiles(MAX_RUN_INPUTS) };
  }
  const uploadIds = kept.flatMap((entry) =>
    entry.state.status === "ready" ? [entry.state.uploadId] : [],
  );
  if (uploadIds.length < kept.length) {
    return { ok: false, message: TEXT.workbench.errors.failedFiles };
  }
  return { ok: true, uploadIds };
}

/**
 * Texto para a região `aria-live` quando arquivos ficam prontos ou falham; o progresso em bytes
 * não é anunciado, só mostrado. `null` quando nada mudou de estado.
 */
export function uploadAnnouncement(
  previous: ReadonlyMap<string, UploadStatus>,
  entries: readonly UploadEntry[],
): string | null {
  const messages = entries.flatMap((entry) => {
    if (previous.get(entry.localId) === entry.state.status) {
      return [];
    }
    switch (entry.state.status) {
      case "ready":
        return [TEXT.workbench.upload.announceReady(entry.file.name)];
      case "failed":
        return [TEXT.workbench.upload.announceFailed(entry.file.name)];
      case "waiting":
      case "sending":
      case "confirming":
      case "canceled":
        return [];
    }
  });
  return messages.length === 0 ? null : messages.join(" ");
}
