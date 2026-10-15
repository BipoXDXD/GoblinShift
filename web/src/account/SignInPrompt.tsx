import { LogIn } from "lucide-react";
import { useId, type ReactElement } from "react";
import { useLocation } from "react-router";

import { TEXT } from "../text";
import { safeReturnTo } from "./signIn";
import { SignInLinks } from "./SignInLinks";

const T = TEXT.account.signIn;

interface SignInPromptProps {
  /** Login de novo no provedor (REAUTH_REQUIRED), voltando para esta mesma tela. */
  readonly reauth?: boolean;
  readonly headingLevel?: 2 | 3;
}

/**
 * "Entre para continuar", no lugar da parte da tela que precisa de sessão. Informativo, sem
 * `role="alert"` nem cor de perigo: estar sem login não é erro de quem usa. A volta é a tela atual.
 */
export function SignInPrompt({
  reauth = false,
  headingLevel = 2,
}: SignInPromptProps): ReactElement {
  const titleId = useId();
  const { pathname, search } = useLocation();
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <section
      aria-labelledby={titleId}
      className="flex gap-3 rounded-lg border-l-4 border-info-fg bg-info-bg p-4 text-info-fg"
    >
      <LogIn aria-hidden="true" className="mt-1 size-4 flex-none" />
      <div className="flex max-w-prose flex-col gap-3">
        <Heading id={titleId} className="font-semibold">
          {reauth ? T.reauthTitle : T.promptTitle}
        </Heading>
        <p>{reauth ? T.reauthBody : T.promptBody}</p>
        <SignInLinks returnTo={safeReturnTo(pathname, search)} reauth={reauth} />
      </div>
    </section>
  );
}
