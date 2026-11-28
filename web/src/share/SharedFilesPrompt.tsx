import { useEffect, useId, useRef, type ReactElement } from "react";

import { Button } from "../components/ui/Button";
import { TEXT } from "../text";
import type { SharedFiles } from "./useSharedFiles";

const T = TEXT.share;

interface SharedFilesPromptProps {
  readonly shared: SharedFiles;
  /** Para onde o foco vai depois de enviar ou descartar, já que a seção some. */
  readonly onDone: () => void;
}

/**
 * Os arquivos que chegaram pelo "Compartilhar", antes de qualquer envio (ADR 064): a pessoa vê os
 * nomes e escolhe enviar ou descartar. Sem arquivos pendentes, não aparece.
 */
export function SharedFilesPrompt({ shared, onDone }: SharedFilesPromptProps): ReactElement | null {
  if (shared.pending.length === 0) {
    return null;
  }
  return <PendingFiles shared={shared} onDone={onDone} />;
}

function PendingFiles({ shared, onDone }: SharedFilesPromptProps): ReactElement {
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  // A seção chega depois da tela, quando a caixa de entrada responde: o foco no título a anuncia.
  useEffect(() => {
    heading.current?.focus();
  }, []);
  const decide = (choice: () => Promise<void>) => () => {
    void choice().then(onDone);
  };
  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-3 rounded-lg border border-line-soft bg-surface p-4"
    >
      <h3 id={headingId} ref={heading} tabIndex={-1} className="font-semibold text-fg">
        {T.receivedHeading}
      </h3>
      <p className="text-fg-muted">{T.receivedHint}</p>
      <ul className="list-disc pl-6">
        {shared.pending.map((name, index) => (
          // Nomes podem repetir; a lista é fixa enquanto existe.
          <li key={`${String(index)}-${name}`}>{name}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-3">
        <Button type="button" variant="secondary" onClick={decide(shared.accept)}>
          {T.sendReceived}
        </Button>
        <Button type="button" variant="tertiary" onClick={decide(shared.discard)}>
          {T.discardReceived}
        </Button>
      </div>
    </section>
  );
}
