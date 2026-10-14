import type { ReactElement } from "react";

import { ApiError } from "../../api/response";
import { isLoginMissing, LoginSoonNotice } from "./LoginSoonNotice";
import { ProblemAlert } from "./ProblemAlert";

interface QueryFailureProps {
  readonly error: Error;
  /** Nível do título do aviso de login, conforme a seção onde ele aparece. */
  readonly headingLevel?: 2 | 3;
}

/**
 * Falha de uma leitura: sem login é o aviso de login em breve (não é erro de quem usa); o resto é
 * o ProblemAlert. Erro que não veio da API é defeito da interface (`internal`).
 */
export function QueryFailure({ error, headingLevel = 2 }: QueryFailureProps): ReactElement {
  if (!(error instanceof ApiError)) {
    return <ProblemAlert failure={{ kind: "internal" }} />;
  }
  return isLoginMissing(error.failure) ? (
    <LoginSoonNotice headingLevel={headingLevel} />
  ) : (
    <ProblemAlert failure={error.failure} />
  );
}
