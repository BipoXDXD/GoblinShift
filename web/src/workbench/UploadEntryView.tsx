import type { ReactElement } from "react";

import { describeFailure } from "../components/feedback/ProblemAlert";
import { formatBytes } from "../format";
import { TEXT } from "../text";
import type { UploadEntry, UploadEntryState } from "../upload/useUploadBatch";

const T = TEXT.workbench.upload;
const PERCENT = 100;

interface UploadEntryViewProps {
  readonly entry: UploadEntry;
  readonly onRetry: (localId: string) => void;
  readonly onRemove: (localId: string) => void;
}

/** Um arquivo da lista: nome, tamanho, estado do envio em texto e as ações possíveis. */
export function UploadEntryView({ entry, onRetry, onRemove }: UploadEntryViewProps): ReactElement {
  const name = entry.file.name;
  return (
    <>
      <span>{name}</span> <span>{formatBytes(entry.file.size)}</span>{" "}
      <UploadState state={entry.state} name={name} />
      {entry.state.status === "failed" && (
        <button
          type="button"
          aria-label={`${T.retry} ${name}`}
          onClick={() => {
            onRetry(entry.localId);
          }}
        >
          {T.retry}
        </button>
      )}
      <button
        type="button"
        aria-label={`${T.remove} ${name}`}
        onClick={() => {
          onRemove(entry.localId);
        }}
      >
        {T.remove}
      </button>
    </>
  );
}

function UploadState({ state, name }: { state: UploadEntryState; name: string }): ReactElement {
  switch (state.status) {
    case "waiting":
      return <span>{T.waiting}</span>;
    case "sending":
      // Percentual legítimo: vem dos bytes enviados (2.2).
      return (
        <>
          <progress
            aria-label={T.progressLabel(name)}
            value={state.loadedBytes}
            max={state.totalBytes}
          />{" "}
          <span>{T.sending(Math.floor((state.loadedBytes / state.totalBytes) * PERCENT))}</span>
        </>
      );
    case "confirming":
      return <span>{T.confirming}</span>;
    case "ready":
      return <span>{T.ready}</span>;
    case "failed":
      return <span>{describeFailure(state.failure).message}</span>;
    case "canceled":
      return <span>{T.canceled}</span>;
  }
}
