import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement, type ReactNode } from "react";

import { useApiClient } from "../api/ApiClientContext";
import { ApiError } from "../api/response";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { EmptyState } from "../components/feedback/EmptyState";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { formatDateTime } from "../format";
import { TEXT } from "../text";
import { deleteRunArtifacts, getArtifact, type RunResults } from "./commands";

const T = TEXT.run.results;

interface ResultsAvailabilityProps {
  readonly runId: string;
  readonly results: RunResults;
  /** Depois de excluir: quem chama põe o foco no título do cartão (o botão some). */
  readonly onDeleted: () => void;
  /** Os downloads, mostrados enquanto os resultados estão disponíveis. */
  readonly children: ReactNode;
}

/**
 * Os downloads só enquanto os resultados existem, e "Excluir resultados" terciária com
 * confirmação em vermelho. O /results lista os artefatos mesmo vencidos ou excluídos; as saídas
 * de uma execução nascem na mesma transação (mesmo vencimento) e só são excluídas juntas, então a
 * disponibilidade de uma basta (uma chamada só).
 *
 * Enquanto ela carrega, a tela diz que está verificando e não oferece os links. Se ela falhar, os
 * links ficam, com o aviso de que a disponibilidade não foi confirmada e um "Verificar de novo":
 * esconder o download por uma falha de leitura bloquearia quem ainda pode baixar, e o download
 * responde por si (410) se os resultados já não existem.
 */
export function ResultsAvailability({
  runId,
  results,
  onDeleted,
  children,
}: ResultsAvailabilityProps): ReactElement {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const [message, setMessage] = useState<string | null>(null);
  // O 202 do DELETE já deixa os resultados indisponíveis (1.6); a tela segue a resposta do
  // comando, não a releitura, que pode falhar ou chegar antes.
  const [deletedHere, setDeletedHere] = useState(false);
  const firstId = results.published ? (results.artifacts[0]?.artifact_id ?? null) : null;
  const artifactKey = ["artifacts", firstId] as const;
  const artifact = useQuery({
    queryKey: artifactKey,
    queryFn: ({ signal }) => getArtifact(client, firstId ?? "", signal),
    enabled: firstId !== null,
    retry: false,
  });
  const removal = useMutation({
    mutationFn: () => deleteRunArtifacts(client, runId),
    onSuccess: () => {
      setDeletedHere(true);
      setMessage(T.removed);
      onDeleted();
      void queryClient.invalidateQueries({ queryKey: artifactKey });
      void queryClient.invalidateQueries({ queryKey: ["runs", runId] });
    },
    onError: (error) => {
      // A execução sumiu (outra aba, exclusão da conta): a tela relê o que ainda existe.
      if (isNotFound(error)) {
        void queryClient.invalidateQueries({ queryKey: ["runs", runId] });
      }
    },
  });
  if (firstId === null) {
    return <>{children}</>;
  }
  const availability = deletedHere ? "deleted" : artifact.data?.availability;
  return (
    <>
      {availability === "deleted" || availability === "expired" || availability === "missing" ? (
        <Unavailable reason={availability} expiresAt={results.expires_at} />
      ) : artifact.isPending ? (
        <p className="text-fg-muted">{T.checking}</p>
      ) : (
        <>
          {artifact.isError && (
            <Unconfirmed
              onRetry={() => {
                void artifact.refetch();
              }}
            />
          )}
          {children}
          <DeleteResults
            pending={removal.isPending}
            error={removal.error}
            onAsk={removal.reset}
            onConfirm={() => {
              removal.mutate();
            }}
          />
        </>
      )}
      <LiveRegion message={message} visuallyHidden />
    </>
  );
}

function isNotFound(error: Error): boolean {
  return (
    error instanceof ApiError &&
    error.failure.kind === "problem" &&
    error.failure.problem.code === "NOT_FOUND"
  );
}

function Unconfirmed({ onRetry }: { readonly onRetry: () => void }): ReactElement {
  return (
    <div className="flex flex-col items-start gap-2">
      <p className="text-warning-fg">{T.unconfirmed}</p>
      <Button type="button" variant="tertiary" onClick={onRetry}>
        {T.checkAgain}
      </Button>
    </div>
  );
}

interface UnavailableProps {
  readonly reason: "deleted" | "expired" | "missing";
  readonly expiresAt: string | null;
}

function Unavailable({ reason, expiresAt }: UnavailableProps): ReactElement {
  if (reason === "deleted") {
    return (
      <EmptyState title={T.deletedTitle} headingLevel={3}>
        <p>{T.deletedHint}</p>
      </EmptyState>
    );
  }
  if (reason === "missing") {
    return (
      <EmptyState title={T.missingTitle} headingLevel={3}>
        <p>{T.missingHint}</p>
      </EmptyState>
    );
  }
  return (
    <EmptyState title={T.expiredTitle} headingLevel={3}>
      <p>{expiresAt === null ? T.expiredHint : T.expiredOn(formatDateTime(expiresAt))}</p>
    </EmptyState>
  );
}

interface DeleteResultsProps {
  readonly pending: boolean;
  readonly error: Error | null;
  readonly onAsk: () => void;
  readonly onConfirm: () => void;
}

function DeleteResults({ pending, error, onAsk, onConfirm }: DeleteResultsProps): ReactElement {
  const [asking, setAsking] = useState(false);
  return (
    <div className="flex flex-col gap-3">
      {error !== null && (
        <FailureNotice
          error={error}
          headingLevel={3}
          messages={{ STATE_CONFLICT: T.removeNotFinished, NOT_FOUND: T.removeRunGone }}
        />
      )}
      <div className="self-start">
        {/* Bloqueado com o pedido em voo: o reset do onAsk zeraria o isPending e liberaria um
            segundo DELETE antes da resposta do primeiro. */}
        <Button
          type="button"
          variant="tertiary"
          aria-disabled={pending}
          onClick={() => {
            onAsk();
            setAsking(true);
          }}
        >
          {pending ? T.removing : T.remove}
        </Button>
      </div>
      <ConfirmDialog
        open={asking}
        title={T.removeTitle}
        confirmLabel={T.removeConfirm}
        cancelLabel={T.removeCancel}
        confirmBlocked={pending}
        onCancel={() => {
          setAsking(false);
        }}
        onConfirm={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        <p>{T.removeBody}</p>
      </ConfirmDialog>
    </div>
  );
}
