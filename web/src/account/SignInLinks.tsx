import type { ReactElement } from "react";

import { buttonClassName } from "../components/ui/Button";
import { TEXT } from "../text";
import { PROVIDERS, signInHref, type Provider } from "./signIn";

interface SignInLinksProps {
  /** Caminho local já conferido por `safeReturnTo`. */
  readonly returnTo: string;
  readonly reauth?: boolean;
  /** O provedor da sessão (GET /me): na reautenticação, só ele vale (o servidor recusa outro). */
  readonly provider?: Provider | null;
}

/**
 * Os dois provedores, com o mesmo peso: nenhum é a escolha "certa". São links de página inteira
 * para o servidor (signIn.ts), não botões com `fetch`. Na reautenticação com o provedor da sessão
 * conhecido, só ele aparece: entrar pelo outro levaria a outra conta, que o servidor recusa.
 */
export function SignInLinks({
  returnTo,
  reauth = false,
  provider: sessionProvider = null,
}: SignInLinksProps): ReactElement {
  const providers = reauth && sessionProvider !== null ? [sessionProvider] : PROVIDERS;
  return (
    <ul className="flex flex-col gap-3 sm:flex-row">
      {providers.map((provider) => (
        <li key={provider}>
          <a
            href={signInHref(provider, { returnTo, reauth })}
            className={buttonClassName({ variant: "secondary", fullWidth: true })}
          >
            {TEXT.account.signIn.providers[provider]}
          </a>
        </li>
      ))}
    </ul>
  );
}
