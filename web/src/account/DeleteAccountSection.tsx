import { useId, useState, type ReactElement } from "react";

import { PATHS } from "../app/paths";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { TEXT } from "../text";
import { deleteAccount } from "./api";
import { useEndSession } from "./useEndSession";

const T = TEXT.account.deletion;

/** A frase confere sem diferenciar maiúsculas nem espaços nas pontas: o objetivo é a intenção. */
function phraseMatches(typed: string): boolean {
  return typed.trim().toLocaleLowerCase(TEXT.locale) === T.phrase;
}

/**
 * Excluir a conta (DELETE /me): ação destrutiva rebaixada na página (terciária) e confirmada num
 * diálogo, onde vira a primária em vermelho e só age com a frase digitada. Pede login recente: o
 * REAUTH_REQUIRED leva a entrar de novo e voltar ao Acesso.
 */
export function DeleteAccountSection(): ReactElement {
  const headingId = useId();
  const phraseId = useId();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const remove = useEndSession(deleteAccount, PATHS.accountDeleted);
  // Fecha sempre, até com o pedido em voo (o navegador pode ter fechado o <dialog>).
  const close = (): void => {
    setOpen(false);
    setTyped("");
  };
  return (
    <Card as="section" aria-labelledby={headingId}>
      <div className="flex flex-col gap-4">
        <h2 id={headingId} className="text-lg font-semibold text-fg">
          {T.heading}
        </h2>
        <p className="max-w-prose text-fg-muted">{T.body}</p>
        {remove.error !== null && !open && <FailureNotice error={remove.error} headingLevel={3} />}
        <div>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              // Reaberto com o pedido em voo, a resposta ainda tem de chegar.
              if (!remove.isPending) {
                remove.reset();
              }
              setOpen(true);
            }}
          >
            {T.open}
          </Button>
        </div>
      </div>
      <ConfirmDialog
        open={open}
        title={T.dialogTitle}
        confirmLabel={remove.isPending ? T.deleting : T.confirm}
        cancelLabel={T.cancel}
        confirmBlocked={!phraseMatches(typed) || remove.isPending}
        busy={remove.isPending}
        onCancel={close}
        onConfirm={() => {
          if (phraseMatches(typed) && !remove.isPending) {
            remove.mutate(undefined, {
              onError: () => {
                setOpen(false);
                setTyped("");
              },
            });
          }
        }}
      >
        <p>{T.dialogBody}</p>
        <div className="flex flex-col gap-2">
          <label htmlFor={phraseId} className="text-sm font-semibold text-fg">
            {T.phraseLabel(T.phrase)}
          </label>
          <input
            id={phraseId}
            data-initial-focus=""
            autoComplete="off"
            spellCheck={false}
            value={typed}
            onChange={(event) => {
              setTyped(event.currentTarget.value);
            }}
          />
        </div>
      </ConfirmDialog>
    </Card>
  );
}
