import { useId, useState, type ReactElement } from "react";

import { PATHS } from "../app/paths";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { TEXT } from "../text";
import { logout, logoutAll, type Me } from "./api";
import { useEndSession } from "./useEndSession";

const T = TEXT.account.access;

/**
 * Sessão atual (5.5): com que conta entrou, Sair e Sair de todos os dispositivos. As duas são
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
