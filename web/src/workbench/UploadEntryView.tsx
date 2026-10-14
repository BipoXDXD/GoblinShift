import { CircleAlert, CircleCheck, Clock, LoaderCircle, Ban, type LucideIcon } from "lucide-react";
import type { ReactElement, ReactNode } from "react";

import { Button } from "../components/ui/Button";
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
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <span className="truncate font-mono text-sm text-fg">{name}</span>{" "}
        <span className="flex-none font-mono text-sm text-fg-muted tabular-nums">
          {formatBytes(entry.file.size)}
        </span>{" "}
      </div>
      <UploadState state={entry.state} name={name} />
      <div className="flex flex-wrap gap-3">
        {entry.state.status === "failed" && (
          <Button
            type="button"
            variant="secondary"
            aria-label={`${T.retry} ${name}`}
            onClick={() => {
              onRetry(entry.localId);
            }}
          >
            {T.retry}
          </Button>
        )}
        <Button
          type="button"
          variant="tertiary"
          aria-label={`${T.remove} ${name}`}
          onClick={() => {
            onRemove(entry.localId);
          }}
        >
          {T.remove}
        </Button>
      </div>
    </div>
  );
}

function UploadState({ state, name }: { state: UploadEntryState; name: string }): ReactElement {
  switch (state.status) {
    case "waiting":
      return <StateLine icon={Clock}>{T.waiting}</StateLine>;
    case "sending":
      // Percentual legítimo: vem dos bytes enviados (2.2).
      return (
        <div className="flex flex-col gap-1">
          <progress
            aria-label={T.progressLabel(name)}
            value={state.loadedBytes}
            max={state.totalBytes}
          />{" "}
          <span className="text-sm text-fg-muted tabular-nums">
            {T.sending(Math.floor((state.loadedBytes / state.totalBytes) * PERCENT))}
          </span>
        </div>
      );
    case "confirming":
      return <StateLine icon={LoaderCircle}>{T.confirming}</StateLine>;
    case "ready":
      return (
        <StateLine icon={CircleCheck} tone="success">
          {T.ready}
        </StateLine>
      );
    case "failed":
      return (
        <StateLine icon={CircleAlert} tone="danger">
          {describeFailure(state.failure).message}
        </StateLine>
      );
    case "canceled":
      return <StateLine icon={Ban}>{T.canceled}</StateLine>;
  }
}

const TONE_TEXT = {
  neutral: "text-fg-muted",
  success: "text-success-fg",
  danger: "font-semibold text-danger-fg",
} as const;

interface StateLineProps {
  readonly icon: LucideIcon;
  readonly tone?: keyof typeof TONE_TEXT;
  readonly children: ReactNode;
}

/** Estado do envio em texto, com ícone de forma própria: nunca só pela cor. */
function StateLine({ icon: Icon, tone = "neutral", children }: StateLineProps): ReactElement {
  return (
    <span className={`flex items-start gap-2 text-sm ${TONE_TEXT[tone]}`}>
      <Icon aria-hidden="true" className="mt-1 size-4 flex-none" />
      <span>{children}</span>
    </span>
  );
}
