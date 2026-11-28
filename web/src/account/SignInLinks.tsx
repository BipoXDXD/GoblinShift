import type { ReactElement } from "react";

import { buttonClassName } from "../components/ui/Button";
import { TEXT } from "../text";
import { PROVIDERS, signInHref, type Provider } from "./signIn";

/**
 * Login comum ou reautenticação. Na reautenticação, `provider` é o da sessão (GET /me): só ele
 * vale, porque o servidor recusa outro; `null` enquanto o /me não chegou (ou falhou).
 */
type SignInMode =
  { readonly kind: "login" } | { readonly kind: "reauth"; readonly provider: Provider | null };

const LOGIN: SignInMode = { kind: "login" };

interface SignInLinksProps {
  /** Caminho local já conferido por `safeReturnTo`. */
  readonly returnTo: string;
  readonly mode?: SignInMode;
}

/**
 * Os dois provedores, com o mesmo peso: nenhum é a escolha "certa". São links de página inteira
 * para o servidor (signIn.ts), não botões com `fetch`. Na reautenticação com o provedor da sessão
 * conhecido, só ele aparece: entrar pelo outro levaria a outra conta, que o servidor recusa.
 */
export function SignInLinks({ returnTo, mode = LOGIN }: SignInLinksProps): ReactElement {
  const reauth = mode.kind === "reauth";
  const providers = mode.kind === "reauth" && mode.provider !== null ? [mode.provider] : PROVIDERS;
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
