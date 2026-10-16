import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement, type ReactNode } from "react";

import { useApiClient } from "../api/ApiClientContext";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { EmptyState } from "../components/feedback/EmptyState";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
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
 * confirmação em vermelho. O /results lista os artefatos mesmo vencidos ou excluídos; como as
 * saídas de uma execução vencem e são excluídas juntas, a disponibilidade de uma basta (uma
 * chamada só). Sem a resposta dela, os links ficam: o download responde por si.
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
  const firstId = results.published ? (results.artifacts[0]?.artifact_id ?? null) : null;
  const artifactKey = ["artifacts", firstId] as const;
  const availability = useQuery({
    queryKey: artifactKey,
    queryFn: ({ signal }) => getArtifact(client, firstId ?? "", signal),
    enabled: firstId !== null,
    retry: false,
  }).data?.availability;
  const removal = useMutation({
    mutationFn: () => deleteRunArtifacts(client, runId),
    onSuccess: async () => {
      setMessage(T.removed);
      await queryClient.invalidateQueries({ queryKey: artifactKey });
      onDeleted();
    },
  });
  return (
    <>
      {availability === "deleted" || availability === "expired" ? (
        <Unavailable reason={availability} />
      ) : (
        <>
          {children}
          {firstId !== null && (
            <DeleteResults
              pending={removal.isPending}
              error={removal.error}
              onAsk={removal.reset}
              onConfirm={() => {
                removal.mutate();
              }}
            />
          )}
        </>
      )}
      <LiveRegion message={message} visuallyHidden />
    </>
  );
}

function Unavailable({ reason }: { readonly reason: "deleted" | "expired" }): ReactElement {
  const deleted = reason === "deleted";
  return (
    <EmptyState title={deleted ? T.deletedTitle : T.expiredTitle} headingLevel={3}>
      <p>{deleted ? T.deletedHint : T.expiredHint}</p>
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
          messages={{ STATE_CONFLICT: T.removeNotFinished }}
        />
      )}
      <div className="self-start">
        <Button
          type="button"
          variant="tertiary"
          onClick={() => {
            onAsk();
            setAsking(true);
          }}
        >
          {T.remove}
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
