import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { acceptTerms, logout, ME_QUERY_KEY, type Me } from "../account/api";
import { returnPathFrom } from "../account/returnParam";
import { useEndSession } from "../account/useEndSession";
import { useMe } from "../account/useMe";
import { useApiClient } from "../api/ApiClientContext";
import { PATHS } from "../app/paths";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button, buttonClassName } from "../components/ui/Button";
import { TEXT } from "../text";

const T = TEXT.terms;

/**
 * Aceite dos termos vigentes (ADR 029). A conta chega aqui pelo bloqueio do Layout ou pelo 403
 * TERMS_NOT_ACCEPTED; aceitar grava a versão que o /me disse ser a vigente e volta para `?volta=`.
 * Recusar é sair: sem o aceite o uso não continua.
 */
export function AcceptTermsPage(): ReactElement {
  const me = useMe();
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <FocusHeading>{T.acceptTitle}</FocusHeading>
      <AcceptBody me={me.data ?? null} error={me.error} />
    </div>
  );
}

function AcceptBody({
  me,
  error,
}: {
  readonly me: Me | null;
  readonly error: Error | null;
}): ReactElement {
  const { search } = useLocation();
  const returnTo = returnPathFrom(search);
  if (me === null) {
    return error === null ? (
      <p className="text-fg-muted">{T.loading}</p>
    ) : (
      <FailureNotice error={error} />
    );
  }
  if (!me.terms.acceptance_required) {
    return (
      <>
        <p>{T.alreadyAccepted(me.terms.accepted_version ?? me.terms.current_version)}</p>
        <p>
          <Link to={returnTo} className={buttonClassName({ variant: "secondary" })}>
            {T.continue}
          </Link>
        </p>
      </>
    );
  }
  return <AcceptForm version={me.terms.current_version} returnTo={returnTo} />;
}

function AcceptForm({
  version,
  returnTo,
}: {
  readonly version: string;
  readonly returnTo: string;
}): ReactElement {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const accept = useMutation({
    mutationFn: () => acceptTerms(client, version),
    onSuccess: async () => {
      // As leituras recusadas com 403 enquanto faltava o aceite voltam a valer.
      await queryClient.invalidateQueries();
      await navigate(returnTo, { replace: true });
    },
    onError: async () => {
      // 409: a versão vigente mudou; o /me novo traz a versão a ler.
      await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
    },
  });
  const signOut = useEndSession(logout, PATHS.home);
  return (
    <>
      <p className="max-w-prose">{T.acceptLead(version)}</p>
      <ul className="flex flex-col gap-2">
        <li>
          <Link to={PATHS.terms} className="inline-flex min-h-11 items-center font-semibold">
            {T.readTerms}
          </Link>
        </li>
        <li>
          <Link to={PATHS.privacy} className="inline-flex min-h-11 items-center font-semibold">
            {T.readPrivacy}
          </Link>
        </li>
      </ul>
      {accept.error !== null && (
        <FailureNotice error={accept.error} messages={{ STATE_CONFLICT: T.versionChanged }} />
      )}
      {signOut.error !== null && <FailureNotice error={signOut.error} />}
      <div className="flex flex-col gap-3 sm:flex-row">
        <Button
          type="button"
          variant="primary"
          aria-disabled={accept.isPending}
          onClick={() => {
            if (!accept.isPending) {
              accept.mutate();
            }
          }}
        >
          {accept.isPending ? T.accepting : T.accept}
        </Button>
        <Button
          type="button"
          variant="tertiary"
          aria-disabled={signOut.isPending}
          onClick={() => {
            signOut.mutate();
          }}
        >
          {T.signOut}
        </Button>
      </div>
    </>
  );
}
