import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { deleteRunArtifacts, type RunResults } from "./commands";

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
 * Os downloads só enquanto há algo para baixar, e "Excluir resultados" terciária com confirmação
 * em vermelho. A disponibilidade de cada referência vem do próprio /results (fonte única): sem
 * nenhuma disponível, a seção mostra o estado vazio com o motivo; com alguma, quem chama mostra
 * cada arquivo com o link ou com o motivo de não ter.
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
  const removal = useMutation({
    mutationFn: () => deleteRunArtifacts(client, runId),
    onSuccess: () => {
      setDeletedHere(true);
      setMessage(T.removed);
      onDeleted();
      void queryClient.invalidateQueries({ queryKey: ["runs", runId] });
    },
    onError: (error) => {
      // A execução sumiu (outra aba, exclusão da conta): a tela relê o que ainda existe.
      if (isNotFound(error)) {
        void queryClient.invalidateQueries({ queryKey: ["runs", runId] });
      }
    },
  });
  if (!results.published || results.artifacts.length === 0) {
    return <>{children}</>;
  }
  const downloadable = !deletedHere && results.artifacts.some(isAvailable);
  return (
    <>
      {downloadable ? (
        <>
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
      ) : (
        <Unavailable
          reason={deletedHere ? "deleted" : sharedReason(results)}
          expiresAt={results.expires_at}
        />
      )}
      <LiveRegion message={message} visuallyHidden />
    </>
  );
}

type Reference = RunResults["artifacts"][number];
type UnavailableReason = "deleted" | "expired" | "other";

function isAvailable(reference: Reference): boolean {
  return reference.availability === "available";
}

/** O motivo comum a todas as referências, ou "other" se elas divergem (ou o motivo é novo). */
function sharedReason(results: RunResults): UnavailableReason {
  const reasons = new Set(results.artifacts.map((reference) => reference.availability));
  const [only] = reasons;
  if (reasons.size === 1 && (only === "deleted" || only === "expired")) {
    return only;
  }
  return "other";
}

function isNotFound(error: Error): boolean {
  return (
    error instanceof ApiError &&
    error.failure.kind === "problem" &&
    error.failure.problem.code === "NOT_FOUND"
  );
}

interface UnavailableProps {
  readonly reason: UnavailableReason;
  readonly expiresAt: string | null;
}

function Unavailable({ reason, expiresAt }: UnavailableProps): ReactElement {
  if (reason === "other") {
    return (
      <EmptyState title={T.unavailableTitle} headingLevel={3}>
        <p>{T.unavailableHint}</p>
      </EmptyState>
    );
  }
  if (reason === "deleted") {
    return (
      <EmptyState title={T.deletedTitle} headingLevel={3}>
        <p>{T.deletedHint}</p>
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
