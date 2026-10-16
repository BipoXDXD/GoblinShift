import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactElement } from "react";

import { useApiClient } from "../api/ApiClientContext";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { TEXT } from "../text";
import type { RunStatus } from "./api";
import { cancelRun, type CancelOutcome } from "./commands";

const T = TEXT.run.cancel;

/** Só a fila e o trabalho em curso param; `stopping` já está parando. */
const CANCELABLE: ReadonlySet<RunStatus> = new Set(["queued", "running"]);

interface CancelRunProps {
  readonly runId: string;
  readonly status: RunStatus;
}

/**
 * Cancelar é secundária e pede confirmação, com o vermelho só no diálogo (RUI cap. 2). Sem
 * atualização otimista (2.2): o anúncio diz o que o servidor respondeu e o polling mostra o estado
 * novo no próximo Retry-After, sem consultar antes dele.
 */
export function CancelRun({ runId, status }: CancelRunProps): ReactElement {
  const client = useApiClient();
  const [asking, setAsking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: () => cancelRun(client, runId),
    onSuccess: (result) => {
      setMessage(outcomeMessage(result));
    },
    onSettled: () => {
      setAsking(false);
    },
  });
  const cancelable = CANCELABLE.has(status);
  const { buttonFocus, messageRef } = useFocusAfterButtonLeaves(cancelable);
  const blocked = mutation.isPending || mutation.isSuccess;
  return (
    <div className="flex flex-col gap-3">
      {cancelable && (
        <div>
          <Button
            type="button"
            variant="secondary"
            aria-disabled={blocked}
            onClick={() => {
              mutation.reset();
              setAsking(true);
            }}
            {...buttonFocus}
          >
            {T.open}
          </Button>
        </div>
      )}
      {mutation.error !== null && <FailureNotice error={mutation.error} />}
      <div ref={messageRef} tabIndex={-1}>
        <LiveRegion message={message} />
      </div>
      <ConfirmDialog
        open={asking}
        title={T.title}
        confirmLabel={T.confirm}
        cancelLabel={T.keepGoing}
        confirmBlocked={mutation.isPending}
        onCancel={() => {
          if (!mutation.isPending) {
            setAsking(false);
          }
        }}
        onConfirm={() => {
          if (!mutation.isPending) {
            mutation.mutate();
          }
        }}
      >
        <p>{T.body}</p>
      </ConfirmDialog>
    </div>
  );
}

/** 202: vai parar; 200: já é terminal, e a fila cancela na hora (`canceled`). */
function outcomeMessage({
  outcome,
  accepted,
}: {
  readonly outcome: CancelOutcome;
  readonly accepted: boolean;
}): string {
  if (accepted) {
    return T.requested;
  }
  return outcome.status === "canceled" ? T.canceled : T.alreadyFinished;
}

/**
 * Quando o polling tira o botão da tela (a execução passou a `stopping`) com o foco nele, o foco
 * iria para o `<body>`; vai para o anúncio do pedido, que continua ali.
 */
function useFocusAfterButtonLeaves(buttonShown: boolean) {
  const buttonFocused = useRef(false);
  const messageRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!buttonShown && buttonFocused.current) {
      buttonFocused.current = false;
      messageRef.current?.focus();
    }
  }, [buttonShown]);
  const buttonFocus = {
    onFocus: () => {
      buttonFocused.current = true;
    },
    onBlur: () => {
      buttonFocused.current = false;
    },
  };
  return { buttonFocus, messageRef };
}
