import type { ReactElement } from "react";

import { buttonClassName } from "../components/ui/Button";
import { TEXT } from "../text";
import { PROVIDERS, signInHref } from "./signIn";

interface SignInLinksProps {
  /** Caminho local já conferido por `safeReturnTo`. */
  readonly returnTo: string;
  readonly reauth?: boolean;
}

/**
 * Os dois provedores, com o mesmo peso: nenhum é a escolha "certa". São links de página inteira
 * para o servidor (signIn.ts), não botões com `fetch`.
 */
export function SignInLinks({ returnTo, reauth = false }: SignInLinksProps): ReactElement {
  return (
    <ul className="flex flex-col gap-3 sm:flex-row">
      {PROVIDERS.map((provider) => (
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
