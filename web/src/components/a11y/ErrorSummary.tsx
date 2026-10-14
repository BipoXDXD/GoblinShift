import { CircleAlert } from "lucide-react";
import { useEffect, useId, useRef, type MouseEvent, type ReactElement } from "react";

import { TEXT } from "../../text";

/** Um erro do formulário; `fieldId` aponta o controle a corrigir, ou `null` se não há um. */
export interface FormIssue {
  readonly fieldId: string | null;
  readonly message: string;
}

interface ErrorSummaryProps {
  readonly issues: readonly FormIssue[];
  /** Número da tentativa de envio; cada tentativa nova com erro devolve o foco ao resumo. */
  readonly attempt: number;
}

/**
 * Resumo dos erros no topo do formulário, que recebe o foco depois de um envio recusado e leva a
 * cada campo por link. Evita que o foco salte para um campo no meio da página sem contexto.
 */
export function ErrorSummary({ issues, attempt }: ErrorSummaryProps): ReactElement | null {
  const container = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const focusedAttempt = useRef<number | null>(null);
  const hasIssues = issues.length > 0;
  useEffect(() => {
    // Uma vez por tentativa: erros que somem e voltam enquanto a pessoa edita não puxam o foco.
    if (hasIssues && focusedAttempt.current !== attempt) {
      focusedAttempt.current = attempt;
      container.current?.focus();
    }
  }, [attempt, hasIssues]);
  if (!hasIssues) {
    return null;
  }
  return (
    <div
      ref={container}
      role="alert"
      aria-labelledby={titleId}
      tabIndex={-1}
      className="flex flex-col gap-3 rounded-lg border-l-4 border-danger-accent bg-danger-bg p-4 text-danger-fg md:p-6"
    >
      <h2 id={titleId} className="flex items-center gap-2 text-base font-semibold">
        <CircleAlert aria-hidden="true" className="size-6 flex-none" />
        {TEXT.errorSummary.title}
      </h2>
      <ul className="flex list-disc flex-col gap-1 pl-12">
        {issues.map((issue, index) => (
          // Mensagens podem se repetir (duas linhas com o mesmo erro); a posição desempata.
          <li key={`${String(index)}-${issue.message}`}>
            {issue.fieldId === null ? (
              issue.message
            ) : (
              <a
                href={`#${issue.fieldId}`}
                className="font-semibold text-danger-fg"
                onClick={focusField(issue.fieldId)}
              >
                {issue.message}
              </a>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// O link de âncora só rola a página; o foco precisa ir ao controle para a pessoa corrigir dali.
function focusField(fieldId: string) {
  return (event: MouseEvent<HTMLAnchorElement>): void => {
    const field = document.getElementById(fieldId);
    if (field === null) {
      return;
    }
    event.preventDefault();
    field.focus();
  };
}
