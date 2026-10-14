import type { ReactElement } from "react";

import type { ApiProblem } from "../../api/problem";
import type { ApiFailure } from "../../api/response";
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

/**
 * Texto próprio da interface para uma falha. O `title` e o `detail` do problem+json não aparecem:
 * o texto vem do `code`, para a mensagem ser estável, traduzível e nunca expor o que o servidor
 * escreveu para quem integra.
 */
export function describeFailure(failure: DisplayableFailure): FailureDescription {
  switch (failure.kind) {
    case "problem":
      return {
        message: withRetryAfter(PROBLEM_MESSAGES[failure.problem.code], failure.retryAfterMs),
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

function withRetryAfter(message: string, retryAfterMs: number | null): string {
  if (retryAfterMs === null || retryAfterMs <= 0) {
    return message;
  }
  return `${message} ${TEXT.failure.retryAfter(Math.ceil(retryAfterMs / MS_PER_SECOND))}`;
}

interface ProblemAlertProps {
  readonly failure: DisplayableFailure;
}

/** Falha mostrada à pessoa, em `role="alert"`, com o código do pedido quando há um. */
export function ProblemAlert({ failure }: ProblemAlertProps): ReactElement {
  const { message, requestId } = describeFailure(failure);
  return (
    <div role="alert">
      <p>{message}</p>
      {requestId !== null && <p>{TEXT.failure.requestId(requestId)}</p>}
    </div>
  );
}
