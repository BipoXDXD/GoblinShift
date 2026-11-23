import { ApiError } from "../api/response";
import { TEXT } from "../text";

const T = TEXT.presets;

/**
 * Teto do nome na API (presets/drafts.py), em code points depois do NFC como lá; conferido aqui
 * para o erro aparecer no campo.
 */
const MAX_NAME_LENGTH = 100;

/** O servidor aponta o campo a partir do corpo (presets.py monta `("body", "name")`). */
const NAME_FIELD = "body.name";
const CONFLICT = 409;

/** Uma forma só para o teto local e para o envio: o servidor mede o texto que recebe. */
export function normalizePresetName(raw: string): string {
  return raw.trim().normalize("NFC");
}

/** Recebe o nome já normalizado, como vai para a API. */
export function presetNameError(finalName: string): string | null {
  if (finalName === "") {
    return T.nameRequired;
  }
  // Code points, não grafemas: é o que o `len` do Python conta no servidor.
  const length = Array.from(finalName).length;
  return length > MAX_NAME_LENGTH ? T.nameTooLong : null;
}

/**
 * 422 só no nome: o erro vai para o campo. Com erro em outro campo junto, o aviso geral fica, para
 * não esconder esse outro erro.
 */
export function isNameRejected(error: Error): boolean {
  if (!(error instanceof ApiError) || error.failure.kind !== "problem") {
    return false;
  }
  const { code, errors } = error.failure.problem;
  return (
    code === "VALIDATION_FAILED" &&
    errors !== null &&
    errors !== undefined &&
    errors.length > 0 &&
    errors.every((fieldError) => fieldError.field === NAME_FIELD)
  );
}

/** 409 do PUT: outro preset da conta já usa o nome. */
export function isNameTaken(error: Error): boolean {
  return (
    error instanceof ApiError &&
    error.failure.kind === "problem" &&
    error.failure.status === CONFLICT &&
    error.failure.problem.code === "STATE_CONFLICT"
  );
}
