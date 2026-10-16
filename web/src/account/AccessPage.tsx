import type { ReactElement } from "react";

import { FocusHeading } from "../components/a11y/FocusHeading";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { TEXT } from "../text";
import type { Me } from "./api";
import { ApiKeysSection } from "./ApiKeysSection";
import { DeleteAccountSection } from "./DeleteAccountSection";
import { SessionSection } from "./SessionSection";
import { SignInPrompt } from "./SignInPrompt";
import { UsageSection } from "./UsageSection";
import { useMe } from "./useMe";

const T = TEXT.account.access;

/**
 * Acesso (5.5): coluna única e séria, sem goblins. Sessão, uso e cotas, chaves e exclusão da
 * conta. Com chave de API (só scripts e o E2E) ficam o uso e o aviso: o resto exige a sessão.
 */
export function AccessPage({ now }: { readonly now: () => number }): ReactElement {
  const me = useMe();
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-8">
      <FocusHeading>{T.title}</FocusHeading>
      <AccessBody me={me.data} error={me.error} now={now} />
    </div>
  );
}

interface AccessBodyProps {
  /** `null` sem sessão; `undefined` carregando ou com falha. */
  readonly me: Me | null | undefined;
  readonly error: Error | null;
  readonly now: () => number;
}

function AccessBody({ me, error, now }: AccessBodyProps): ReactElement {
  if (me === undefined) {
    return error === null ? (
      <p className="text-fg-muted">{T.loading}</p>
    ) : (
      <FailureNotice error={error} />
    );
  }
  if (me === null) {
    return <SignInPrompt />;
  }
  if (me.via === "api_key") {
    return (
      <>
        <p className="max-w-prose rounded-lg bg-info-bg p-4 text-info-fg">{T.viaApiKey}</p>
        <UsageSection me={me} />
      </>
    );
  }
  return (
    <>
      <SessionSection me={me} />
      <UsageSection me={me} />
      <ApiKeysSection now={now} />
      <DeleteAccountSection />
    </>
  );
}
