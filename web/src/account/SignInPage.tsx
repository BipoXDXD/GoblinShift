import type { ReactElement } from "react";
import { Link, useLocation } from "react-router";

import { PATHS } from "../app/paths";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { TEXT } from "../text";
import { returnPathFrom } from "./returnParam";
import { SignInLinks } from "./SignInLinks";
import { useMe } from "./useMe";

const T = TEXT.account.signIn;

/**
 * Entrar: os dois provedores e os textos legais. Quem já tem sessão vê com que conta entrou, sem
 * os botões, para não abrir um segundo login por engano.
 */
export function SignInPage(): ReactElement {
  const { search } = useLocation();
  const me = useMe();
  const returnTo = returnPathFrom(search);
  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-6">
      <FocusHeading>{T.title}</FocusHeading>
      {me.data?.via === "session" ? (
        <p className="flex flex-col gap-2">
          <span>{T.alreadyIn(me.data.display_name)}</span>
          <Link to={PATHS.access} className="inline-flex min-h-11 items-center font-semibold">
            {T.goToAccess}
          </Link>
        </p>
      ) : (
        <>
          <p className="text-fg-muted">{T.lead}</p>
          <SignInLinks returnTo={returnTo} />
          <p className="text-sm text-fg-muted">
            {T.legal} <Link to={PATHS.terms}>{T.legalTerms}</Link> {T.legalAnd}{" "}
            <Link to={PATHS.privacy}>{T.legalPrivacy}</Link>.
          </p>
        </>
      )}
    </div>
  );
}
