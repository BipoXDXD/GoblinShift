import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CircleCheck, Clock } from "lucide-react";
import { useId, useState, type ReactElement } from "react";
import { Link } from "react-router";

import { useApiClient } from "../api/ApiClientContext";
import { ApiError } from "../api/response";
import { PATHS } from "../app/paths";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { EmptyState } from "../components/feedback/EmptyState";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { formatDateTime } from "../format";
import { TEXT } from "../text";
import {
  API_KEYS_QUERY_KEY,
  listApiKeys,
  revokeApiKey,
  type ApiKey,
  type CreatedApiKey,
} from "./api";
import { keyStatus, type KeyStatus } from "./keys";
import { NewKeyForm } from "./NewKeyForm";
import { NewKeyReveal } from "./NewKeyReveal";

const T = TEXT.account.keys;

/**
 * Chaves de API (5.5): a lista, "Nova chave" secundária e "Revogar" terciária com confirmação em
 * vermelho. O segredo da chave criada fica só no estado desta seção.
 */
export function ApiKeysSection({ now }: { readonly now: () => number }): ReactElement {
  const headingId = useId();
  const client = useApiClient();
  const keys = useQuery({
    queryKey: API_KEYS_QUERY_KEY,
    queryFn: ({ signal }) => listApiKeys(client, signal),
  });
  const [formOpen, setFormOpen] = useState(false);
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const revoke = useRevoke();
  const openForm = (): void => {
    setCreated(null);
    setFormOpen(true);
  };
  return (
    <Card as="section" aria-labelledby={headingId}>
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <h2 id={headingId} className="text-lg font-semibold text-fg">
            {T.heading}
          </h2>
          <p className="max-w-prose text-fg-muted">{T.intro}</p>
          <p>
            <Link to={PATHS.apiGuide} className="inline-flex min-h-11 items-center font-semibold">
              {T.guideLink}
            </Link>
          </p>
        </div>
        {created !== null && (
          <NewKeyReveal
            apiKey={created}
            onDone={() => {
              setCreated(null);
            }}
          />
        )}
        {formOpen && (
          <NewKeyForm
            onCreated={(key) => {
              setFormOpen(false);
              setCreated(key);
            }}
            onCancel={() => {
              setFormOpen(false);
            }}
          />
        )}
        <KeysBody
          keys={keys.data ?? null}
          error={keys.error}
          nowMs={now()}
          formOpen={formOpen}
          onNewKey={openForm}
          onRevoke={revoke.ask}
        />
        {revoke.failure !== null && <FailureNotice error={revoke.failure} headingLevel={3} />}
        <LiveRegion message={revoke.message} />
      </div>
      <ConfirmDialog
        open={revoke.target !== null}
        title={T.revokeTitle(revoke.target?.public_id ?? "")}
        confirmLabel={T.revokeConfirm}
        cancelLabel={T.cancel}
        confirmBlocked={revoke.pending}
        busy={revoke.pending}
        onCancel={revoke.cancel}
        onConfirm={revoke.confirm}
      >
        <p>{T.revokeBody}</p>
      </ConfirmDialog>
    </Card>
  );
}

interface KeysBodyProps {
  readonly keys: readonly ApiKey[] | null;
  readonly error: Error | null;
  readonly nowMs: number;
  readonly formOpen: boolean;
  readonly onNewKey: () => void;
  readonly onRevoke: (key: ApiKey) => void;
}

function KeysBody({
  keys,
  error,
  nowMs,
  formOpen,
  onNewKey,
  onRevoke,
}: KeysBodyProps): ReactElement | null {
  if (keys === null) {
    return error === null ? (
      <p className="text-fg-muted">{T.loading}</p>
    ) : (
      <FailureNotice error={error} headingLevel={3} />
    );
  }
  const newKeyButton = formOpen ? null : (
    <Button type="button" variant="secondary" onClick={onNewKey}>
      {T.newKey}
    </Button>
  );
  if (keys.length === 0) {
    return formOpen ? null : (
      <EmptyState title={T.empty} headingLevel={3}>
        <p>{T.emptyHint}</p>
        {newKeyButton}
      </EmptyState>
    );
  }
  return (
    <>
      <ul aria-label={T.listLabel} className="flex flex-col gap-3">
        {keys.map((key) => (
          <KeyItem key={key.id} apiKey={key} status={keyStatus(key, nowMs)} onRevoke={onRevoke} />
        ))}
      </ul>
      {newKeyButton !== null && <div>{newKeyButton}</div>}
    </>
  );
}

interface KeyItemProps {
  readonly apiKey: ApiKey;
  readonly status: KeyStatus;
  readonly onRevoke: (key: ApiKey) => void;
}

function KeyItem({ apiKey, status, onRevoke }: KeyItemProps): ReactElement {
  return (
    <li className="flex flex-col gap-2 rounded-lg bg-surface-sunken p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-col gap-1">
        <p className="flex flex-wrap items-baseline gap-x-3">
          <span className="font-mono font-semibold">{apiKey.public_id}</span>
          <KeyStatusLabel status={status} />
        </p>
        <p className="text-sm text-fg-muted">{apiKey.scopes.join(", ")}</p>
        <p className="text-sm text-fg-muted">{keyDates(apiKey, status).join(" · ")}</p>
      </div>
      {status === "active" && (
        <Button
          type="button"
          variant="tertiary"
          onClick={() => {
            onRevoke(apiKey);
          }}
        >
          {T.revoke} <span className="sr-only">{T.revokeOf(apiKey.public_id)}</span>
        </Button>
      )}
    </li>
  );
}

function keyDates(key: ApiKey, status: KeyStatus): string[] {
  const end =
    status === "revoked" && key.revoked_at !== null
      ? T.revokedAt(formatDateTime(key.revoked_at))
      : status === "expired"
        ? T.expiredAt(formatDateTime(key.expires_at))
        : T.expiresAt(formatDateTime(key.expires_at));
  const lastUse =
    key.last_used_at === null ? T.neverUsed : T.lastUsed(formatDateTime(key.last_used_at));
  return [T.createdAt(formatDateTime(key.created_at)), end, lastUse];
}

/** Estado com ícone e texto: a cor nunca é o único sinal (WCAG 1.4.1). */
function KeyStatusLabel({ status }: { readonly status: KeyStatus }): ReactElement {
  switch (status) {
    case "active":
      return (
        <span className="inline-flex items-center gap-1 text-sm font-semibold text-success-fg">
          <CircleCheck aria-hidden="true" className="size-4" />
          {T.statuses.active}
        </span>
      );
    case "expired":
      return (
        <span className="inline-flex items-center gap-1 text-sm font-semibold text-fg-muted">
          <Clock aria-hidden="true" className="size-4" />
          {T.statuses.expired}
        </span>
      );
    case "revoked":
      return (
        <span className="inline-flex items-center gap-1 text-sm font-semibold text-fg-muted">
          <Ban aria-hidden="true" className="size-4" />
          {T.statuses.revoked}
        </span>
      );
  }
}

interface Revoke {
  readonly target: ApiKey | null;
  readonly pending: boolean;
  readonly failure: Error | null;
  readonly message: string | null;
  readonly ask: (key: ApiKey) => void;
  readonly cancel: () => void;
  readonly confirm: () => void;
}

/** Revogar com confirmação. A chave que já não existe (404) só some da lista, com um aviso. */
function useRevoke(): Revoke {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<ApiKey | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (key: ApiKey) => revokeApiKey(client, key.id),
    onSuccess: (_result, key) => {
      setMessage(T.revoked(key.public_id));
    },
    onError: (error) => {
      if (isNotFound(error)) {
        setMessage(T.notFound);
      }
    },
    onSettled: async () => {
      setTarget(null);
      await queryClient.invalidateQueries({ queryKey: API_KEYS_QUERY_KEY });
    },
  });
  const failure = mutation.error !== null && !isNotFound(mutation.error) ? mutation.error : null;
  return {
    target,
    pending: mutation.isPending,
    failure,
    message,
    // Reaberto com o pedido em voo (o navegador fechou o <dialog>), a resposta ainda tem de chegar.
    ask: (key) => {
      if (!mutation.isPending) {
        mutation.reset();
        setMessage(null);
      }
      setTarget(key);
    },
    // Fecha sempre: o <dialog> pode já ter fechado, e o estado não pode divergir dele.
    cancel: () => {
      setTarget(null);
    },
    confirm: () => {
      if (target !== null && !mutation.isPending) {
        mutation.mutate(target);
      }
    },
  };
}

function isNotFound(error: Error): boolean {
  return (
    error instanceof ApiError &&
    error.failure.kind === "problem" &&
    error.failure.problem.code === "NOT_FOUND"
  );
}
