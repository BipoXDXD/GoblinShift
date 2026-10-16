import { CircleAlert } from "lucide-react";
import type { ReactElement } from "react";

import { describeFailure } from "../components/feedback/ProblemAlert";
import { Button } from "../components/ui/Button";
import { TEXT } from "../text";
import type { FailedDeletion } from "../upload/useUploadBatch";

const T = TEXT.workbench.removal;

interface DeletionFailuresProps {
  readonly failures: readonly FailedDeletion[];
  readonly onRetry: () => void;
}

/**
 * Exclusões no servidor que falharam. O arquivo já saiu do lote, então o aviso diz quais eram, o
 * motivo da última falha e o que acontece se a pessoa não fizer nada.
 */
export function DeletionFailures({
  failures,
  onRetry,
}: DeletionFailuresProps): ReactElement | null {
  const [first] = failures;
  if (first === undefined) {
    return null;
  }
  const one = failures.length === 1;
  const title = one ? T.deletionFailedOne(first.name) : T.deletionFailedMany(failures.length);
  const reason = describeFailure(failures.at(-1)?.failure ?? first.failure).message;
  return (
    <div
      role="alert"
      className="flex gap-3 rounded-lg border-l-4 border-danger-accent bg-danger-bg p-4 text-danger-fg"
    >
      <CircleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
      <div className="flex max-w-prose flex-col gap-2">
        <p className="font-semibold">{title}</p>
        {!one && (
          <ul className="flex flex-col gap-1 font-mono text-sm">
            {failures.map((failed) => (
              <li key={failed.uploadId}>{failed.name}</li>
            ))}
          </ul>
        )}
        <p>{reason}</p>
        <p className="text-sm">{one ? T.deletionFailedHintOne : T.deletionFailedHintMany}</p>
        <div>
          <Button type="button" variant="secondary" onClick={onRetry}>
            {T.retryDeletion}
          </Button>
        </div>
      </div>
    </div>
  );
}
