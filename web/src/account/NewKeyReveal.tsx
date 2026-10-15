import { Check, Copy, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactElement } from "react";

import { LiveRegion } from "../components/a11y/LiveRegion";
import { Button } from "../components/ui/Button";
import { TEXT } from "../text";
import type { CreatedApiKey } from "./api";

const T = TEXT.account.keys;

type CopyState = "idle" | "copied" | "failed";

interface NewKeyRevealProps {
  readonly apiKey: CreatedApiKey;
  readonly onDone: () => void;
}

/**
 * O segredo da chave recém-criada, mostrado uma vez: vive só no estado desta tela (nunca em
 * storage, cache, URL ou log) e some ao clicar em "Já copiei" ou ao sair do Acesso. O foco vem para
 * o título, para o leitor de tela anunciar que a chave saiu.
 */
export function NewKeyReveal({ apiKey, onDone }: NewKeyRevealProps): ReactElement {
  const headingId = useId();
  const fieldId = useId();
  const warningId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [copy, setCopy] = useState<CopyState>("idle");
  useEffect(() => {
    heading.current?.focus();
  }, []);

  const copyToken = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(apiKey.token);
      setCopy("copied");
    } catch {
      // Sem permissão ou sem a API de área de transferência: o campo continua selecionável.
      setCopy("failed");
    }
  };

  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-4 rounded-lg border-l-4 border-success-fg bg-success-bg p-6 text-success-fg"
    >
      <h3 id={headingId} ref={heading} tabIndex={-1} className="font-semibold">
        {T.createdHeading}: <span className="font-mono">{apiKey.public_id}</span>
      </h3>
      <p id={warningId} className="flex items-start gap-2 font-semibold">
        <TriangleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
        {T.createdWarning}
      </p>
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId} className="text-sm font-semibold">
          {T.tokenLabel}
        </label>
        <input
          id={fieldId}
          readOnly
          value={apiKey.token}
          aria-describedby={warningId}
          autoComplete="off"
          spellCheck={false}
          className="font-mono"
          onFocus={(event) => {
            event.currentTarget.select();
          }}
        />
      </div>
      <div className="flex flex-col gap-3 sm:flex-row">
        <Button type="button" variant="secondary" onClick={() => void copyToken()}>
          {copy === "copied" ? (
            <Check aria-hidden="true" className="size-4" />
          ) : (
            <Copy aria-hidden="true" className="size-4" />
          )}
          {T.copy}
        </Button>
        <Button type="button" variant="tertiary" onClick={onDone}>
          {T.done}
        </Button>
      </div>
      <LiveRegion message={copyMessage(copy)} />
    </section>
  );
}

function copyMessage(state: CopyState): string | null {
  switch (state) {
    case "idle":
      return null;
    case "copied":
      return T.copied;
    case "failed":
      return T.copyFailed;
  }
}
