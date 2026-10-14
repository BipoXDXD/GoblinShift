import { CircleAlert } from "lucide-react";
import type { ReactElement } from "react";

import type { ApiProblem } from "../../api/problem";
import type { ApiFailure } from "../../api/response";
import { MAX_RETRY_AFTER_MS } from "../../api/retryAfter";
import { TEXT } from "../../text";

/** Falha a mostrar: da API, ou `internal` para um defeito da própria interface. */
export type DisplayableFailure = ApiFailure | { kind: "internal" };

export interface FailureDescription {
  readonly message: string;
  /** `request_id` do problem+json, para a pessoa citar ao pedir suporte. */
  readonly requestId: string | null;
}

const PROBLEM_MESSAGES: Record<ApiProblem["code"], string> = TEXT.failure.problems;
const MS_PER_SECOND = 1_000;
const MS_PER_MINUTE = 60 * MS_PER_SECOND;

/** Texto da tela para alguns códigos, quando o geral não diz o bastante ali (409 de chaves). */
export type ProblemMessages = Partial<Record<ApiProblem["code"], string>>;

/**
 * Texto próprio da interface para uma falha. O `title` e o `detail` do problem+json não aparecem:
 * o texto vem do `code`, para a mensagem ser estável, traduzível e nunca expor o que o servidor
 * escreveu para quem integra.
 */
export function describeFailure(
  failure: DisplayableFailure,
  messages: ProblemMessages = {},
): FailureDescription {
  switch (failure.kind) {
    case "problem":
      return {
        message: withRetryAfter(
          messages[failure.problem.code] ?? PROBLEM_MESSAGES[failure.problem.code],
          failure.retryAfterMs,
        ),
        requestId: failure.problem.request_id,
      };
    case "unexpected-response":
      return { message: TEXT.failure.unexpected, requestId: null };
    case "network":
      return { message: TEXT.failure.network, requestId: null };
    case "timeout":
      return { message: TEXT.failure.timeout, requestId: null };
    case "internal":
      return { message: TEXT.failure.internal, requestId: null };
  }
}

/**
 * Acrescenta a espera pedida. No teto do parse (5 min) o valor real pode ser de horas, como o da
 * cota diária: a frase seria uma promessa falsa, então fica só o texto do código.
 */
function withRetryAfter(message: string, retryAfterMs: number | null): string {
  if (retryAfterMs === null || retryAfterMs <= 0 || retryAfterMs >= MAX_RETRY_AFTER_MS) {
    return message;
  }
  const wait =
    retryAfterMs < MS_PER_MINUTE
      ? TEXT.failure.retryAfter(Math.ceil(retryAfterMs / MS_PER_SECOND))
      : TEXT.failure.retryAfterMinutes(Math.ceil(retryAfterMs / MS_PER_MINUTE));
  return `${message} ${wait}`;
}

interface ProblemAlertProps {
  readonly failure: DisplayableFailure;
  readonly messages?: ProblemMessages;
}

/** Falha mostrada à pessoa, em `role="alert"`, com o código do pedido quando há um. */
export function ProblemAlert({ failure, messages }: ProblemAlertProps): ReactElement {
  const { message, requestId } = describeFailure(failure, messages);
  return (
    <div
      role="alert"
      className="flex gap-3 rounded-lg border-l-4 border-danger-accent bg-danger-bg p-4 text-danger-fg"
    >
      <CircleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
      <div className="flex flex-col gap-1">
        <p className="font-semibold">{message}</p>
        {requestId !== null && (
          <p className="font-mono text-sm">{TEXT.failure.requestId(requestId)}</p>
        )}
      </div>
    </div>
  );
}
