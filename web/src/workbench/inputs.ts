import type { InputLimits } from "../catalog/inputLimits";
import { formatBytes } from "../format";
import { TEXT } from "../text";
import type { UploadEntry, UploadEntryState } from "../upload/useUploadBatch";

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

/**
 * Ids dos uploads prontos, na ordem da lista, ou o motivo de ainda não dar para executar. Os
 * limites são os da versão da receita no catálogo.
 */
export function runInputs(entries: readonly UploadEntry[], limits: InputLimits): RunInputs {
  const kept = entries.filter((entry) => entry.state.status !== "canceled");
  if (kept.length === 0) {
    return { ok: false, message: TEXT.workbench.errors.noFiles };
  }
  if (kept.some((entry) => IN_PROGRESS.has(entry.state.status))) {
    return { ok: false, message: TEXT.workbench.errors.uploading };
  }
  if (kept.length > limits.maxInputs) {
    return { ok: false, message: TEXT.workbench.errors.tooManyFiles(limits.maxInputs) };
  }
  const oversized = firstAbove(kept, limits.maxFileBytes);
  if (oversized !== null) {
    return {
      ok: false,
      message: TEXT.workbench.errors.fileTooLarge(
        oversized.entry.file.name,
        formatBytes(oversized.entry.file.size),
        formatBytes(oversized.maxFileBytes),
      ),
    };
  }
  const totalBytes = kept.reduce((sum, entry) => sum + entry.file.size, 0);
  if (limits.maxTotalBytes !== null && totalBytes > limits.maxTotalBytes) {
    return {
      ok: false,
      message: TEXT.workbench.errors.tooManyBytes(
        formatBytes(totalBytes),
        formatBytes(limits.maxTotalBytes),
      ),
    };
  }
  const uploadIds = kept.flatMap((entry) =>
    entry.state.status === "ready" ? [entry.state.uploadId] : [],
  );
  if (uploadIds.length < kept.length) {
    return { ok: false, message: TEXT.workbench.errors.failedFiles };
  }
  return { ok: true, uploadIds };
}

/** Primeira entrada acima do teto por arquivo da versão; `null` sem teto ou sem excesso. */
function firstAbove(
  entries: readonly UploadEntry[],
  maxFileBytes: number | null,
): { readonly entry: UploadEntry; readonly maxFileBytes: number } | null {
  if (maxFileBytes === null) {
    return null;
  }
  const entry = entries.find((candidate) => candidate.file.size > maxFileBytes);
  return entry === undefined ? null : { entry, maxFileBytes };
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
