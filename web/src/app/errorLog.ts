import { isRouteErrorResponse } from "react-router";

import { ApiError } from "../api/response";

/**
 * O que de um erro pode ir ao console: só campos conhecidos. O `detail`, o `instance` e os `errors`
 * do problem+json, as propriedades extras e a stack podem carregar dado do usuário.
 */
export function loggableError(error: unknown): Record<string, unknown> {
  if (error instanceof ApiError) {
    const { failure } = error;
    switch (failure.kind) {
      case "problem":
        return {
          name: error.name,
          kind: failure.kind,
          status: failure.status,
          code: failure.problem.code,
          request_id: failure.problem.request_id,
        };
      case "unexpected-response":
        return { name: error.name, kind: failure.kind, status: failure.status };
      case "network":
      case "timeout":
        return { name: error.name, kind: failure.kind };
    }
  }
  if (isRouteErrorResponse(error)) {
    return { type: "route-error-response", status: error.status };
  }
  if (error instanceof Error) {
    return { name: error.name, message: error.message };
  }
  return { type: typeof error };
}
