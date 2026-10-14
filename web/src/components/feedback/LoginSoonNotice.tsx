import { LogIn } from "lucide-react";
import { useId, type ReactElement } from "react";

import type { ApiFailure } from "../../api/response";
import { TEXT } from "../../text";

const T = TEXT.loginSoon;

/**
 * A falha é só a falta de login: sem sessão, a API responde 401 AUTHENTICATION_REQUIRED. O login
 * pela interface chega com a fatia 6; até lá isso não é erro de quem usa, e sim o estado normal.
 */
export function isLoginMissing(failure: ApiFailure): boolean {
  return failure.kind === "problem" && failure.problem.code === "AUTHENTICATION_REQUIRED";
}

/**
 * Aviso de que o login chega em breve, no lugar do erro de sessão. Informativo, sem `role="alert"`
 * e sem cor de perigo: não há nada a corrigir. Vira a tela "Entre para continuar" com a fatia 6.
 */
export function LoginSoonNotice(): ReactElement {
  const titleId = useId();
  return (
    <section
      aria-labelledby={titleId}
      className="flex gap-3 rounded-lg border-l-4 border-info-fg bg-info-bg p-4 text-info-fg"
    >
      <LogIn aria-hidden="true" className="mt-1 size-4 flex-none" />
      <div className="flex max-w-prose flex-col gap-1">
        <h2 id={titleId} className="font-semibold">
          {T.title}
        </h2>
        <p>{T.body}</p>
      </div>
    </section>
  );
}
