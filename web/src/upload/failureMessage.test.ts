import { describe, expect, it } from "vitest";

import type { ApiProblem } from "../api/problem";
import { TEXT } from "../text";
import { uploadFailureMessage } from "./failureMessage";
import type { UploadFailure } from "./useUploadBatch";

function validationFailed(code: string): UploadFailure {
  const problem: ApiProblem = {
    type: "https://goblinshift.dev/problems/validation-failed",
    title: "Validação falhou",
    status: 422,
    detail: "A requisição tem campos inválidos; veja `errors`.",
    instance: "/api/v1/uploads/1/content",
    code: "VALIDATION_FAILED",
    request_id: "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a",
    errors: [{ field: "body", code, message: "recusado" }],
  };
  return { kind: "problem", status: 422, problem, retryAfterMs: null };
}

describe("uploadFailureMessage", () => {
  it("diz que o conteúdo não parece vídeo no signature_mismatch", () => {
    expect(uploadFailureMessage(validationFailed("signature_mismatch"))).toBe(
      "Este arquivo não parece um vídeo. Acima do tamanho comum, só vídeos são aceitos.",
    );
  });

  it("pede novo envio no size_mismatch", () => {
    expect(uploadFailureMessage(validationFailed("size_mismatch"))).toBe(
      "O arquivo chegou com tamanho diferente do esperado. Envie de novo.",
    );
  });

  it("mantém o texto do código para outro motivo", () => {
    expect(uploadFailureMessage(validationFailed("invalid"))).toBe(
      TEXT.failure.problems.VALIDATION_FAILED,
    );
  });
});
