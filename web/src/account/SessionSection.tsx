import { Check, Copy } from "lucide-react";
import { useId, useState, type ReactElement } from "react";

import { PATHS } from "../app/paths";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { TEXT } from "../text";
import { logout, logoutAll, type Me } from "./api";
import { useCopyToClipboard } from "./useCopyToClipboard";
import { useEndSession } from "./useEndSession";

const T = TEXT.account.access;

/**
 * Sessão atual (5.5): com que conta entrou e o identificador dela (pedido nos contatos com a
 * privacidade), Sair e Sair de todos os dispositivos. As duas são
 * secundárias; "de todos" também revoga as chaves, então pede confirmação.
 */
export function SessionSection({ me }: { readonly me: Me }): ReactElement {
  const headingId = useId();
  const [confirming, setConfirming] = useState(false);
  const signOut = useEndSession(logout, PATHS.home);
  const signOutAll = useEndSession(logoutAll, PATHS.home);
  const pending = signOut.isPending || signOutAll.isPending;
  const failure = signOut.error ?? signOutAll.error;
  return (
    <Card as="section" aria-labelledby={headingId}>
      <div className="flex flex-col gap-4">
        <h2 id={headingId} className="text-lg font-semibold text-fg">
          {T.sessionHeading}
        </h2>
        <p>{T.signedInAs(me.display_name)}</p>
        <AccountId id={me.id} />
        {failure !== null && <FailureNotice error={failure} headingLevel={3} />}
        <div className="flex flex-col gap-3 sm:flex-row">
          <Button
            type="button"
            variant="secondary"
            aria-disabled={pending}
            onClick={() => {
              signOut.mutate();
            }}
          >
            {signOut.isPending ? T.signingOut : T.signOut}
          </Button>
          <Button
            type="button"
            variant="tertiary"
            aria-disabled={pending}
            onClick={() => {
              setConfirming(true);
            }}
          >
            {T.signOutAll}
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={confirming}
        title={T.signOutAllTitle}
        confirmLabel={T.signOutAllConfirm}
        cancelLabel={T.cancel}
        confirmBlocked={signOutAll.isPending}
        onCancel={() => {
          setConfirming(false);
        }}
        onConfirm={() => {
          setConfirming(false);
          signOutAll.mutate();
        }}
      >
        <p>{T.signOutAllBody}</p>
      </ConfirmDialog>
    </Card>
  );
}

/** O identificador da conta, selecionável e com botão de copiar (mesmo padrão de NewKeyReveal). */
function AccountId({ id }: { readonly id: string }): ReactElement {
  const { state: copy, copy: copyText } = useCopyToClipboard();

  return (
    <div className="flex flex-col gap-2">
      <dl className="flex flex-col gap-1">
        <dt className="text-sm font-semibold">{T.accountIdLabel}</dt>
        <dd>
          <code className="font-mono break-all select-all">{id}</code>
        </dd>
      </dl>
      <p className="text-sm text-fg-muted">{T.accountIdHint}</p>
      <div>
        <Button type="button" variant="tertiary" onClick={() => void copyText(id)}>
          {copy === "copied" ? (
            <Check aria-hidden="true" className="size-4" />
          ) : (
            <Copy aria-hidden="true" className="size-4" />
          )}
          {T.copyAccountId}
        </Button>
      </div>
      <LiveRegion message={copy === "copied" ? T.accountIdCopied : null} />
      <LiveRegion
        message={copy === "failed" ? T.accountIdCopyFailed : null}
        politeness="assertive"
      />
    </div>
  );
}
