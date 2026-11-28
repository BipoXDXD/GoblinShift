import { describeFailure } from "../components/feedback/ProblemAlert";
import { TEXT } from "../text";
import type { UploadFailure } from "./useUploadBatch";

const CONTENT_ERRORS: Readonly<Record<string, string>> = TEXT.workbench.upload.contentErrors;

/**
 * Texto da falha de um arquivo da lista. O 422 do envio do conteúdo diz o motivo em
 * `errors[].code` (`signature_mismatch`, `size_mismatch`); sem texto próprio, vale o do código.
 */
export function uploadFailureMessage(failure: UploadFailure): string {
  if (failure.kind === "problem" && failure.problem.code === "VALIDATION_FAILED") {
    for (const error of failure.problem.errors ?? []) {
      const message = CONTENT_ERRORS[error.code];
      if (message !== undefined) {
        return message;
      }
    }
  }
  return describeFailure(failure).message;
}
