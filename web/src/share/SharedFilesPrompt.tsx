import { useId, type ReactElement } from "react";

import { Button } from "../components/ui/Button";
import { TEXT } from "../text";
import type { SharedFiles } from "./useSharedFiles";

const T = TEXT.share;

/**
 * Os arquivos que chegaram pelo "Compartilhar", antes de qualquer envio (ADR 064): a pessoa vê os
 * nomes e escolhe enviar ou descartar. Sem arquivos pendentes, não aparece.
 */
export function SharedFilesPrompt({
  shared,
}: {
  readonly shared: SharedFiles;
}): ReactElement | null {
  const headingId = useId();
  if (shared.pending.length === 0) {
    return null;
  }
  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-3 rounded-lg border border-line-soft bg-surface p-4"
    >
      <h3 id={headingId} className="font-semibold text-fg">
        {T.receivedHeading}
      </h3>
      <p className="text-fg-muted">{T.receivedHint}</p>
      <ul className="list-disc pl-6">
        {shared.pending.map((file, index) => (
          // Nomes podem repetir; a lista é fixa enquanto existe.
          <li key={`${String(index)}-${file.name}`}>{file.name}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-3">
        <Button type="button" variant="secondary" onClick={shared.accept}>
          {T.sendReceived}
        </Button>
        <Button type="button" variant="tertiary" onClick={shared.discard}>
          {T.discardReceived}
        </Button>
      </div>
    </section>
  );
}
