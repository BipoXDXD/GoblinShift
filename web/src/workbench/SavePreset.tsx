import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type KeyboardEvent, type ReactElement } from "react";

import { useApiClient } from "../api/ApiClientContext";
import { Field } from "../components/a11y/Field";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { createPreset, PRESETS_QUERY_KEY, type PresetDraft } from "../presets/api";
import type { RecipeDraft, RecipeId } from "../recipes/types";
import { TEXT } from "../text";

const T = TEXT.presets;
/**
 * Teto do nome na API (presets/drafts.py), em code points depois do NFC como lá; conferido aqui
 * para o erro aparecer no campo.
 */
const MAX_NAME_LENGTH = 100;

/**
 * "Salvar preset" da Bancada: secundária, ao lado da primária Executar. Guarda os parâmetros do
 * formulário como estão; com parâmetros inválidos, pede a correção em vez de abrir o diálogo.
 */
export function SavePreset({ draft }: { readonly draft: RecipeDraft<RecipeId> }): ReactElement {
  const nameId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const save = useSave();

  // Fecha sempre, até com o pedido em voo (o navegador pode ter fechado o <dialog>): a resposta
  // que chegar depois aparece ao reabrir, e só então o erro antigo é descartado.
  const close = (): void => {
    setOpen(false);
    setNameError(null);
    if (!save.isPending) {
      save.reset();
    }
  };

  const submit = (): void => {
    if (!draft.ok || save.isPending) {
      return;
    }
    const error = nameErrorOf(name);
    setNameError(error);
    if (error !== null) {
      return;
    }
    save.mutate(
      { name: name.trim(), ...draft.command },
      {
        onSuccess: (preset) => {
          setMessage(T.saved(preset.name));
          setName("");
          setOpen(false);
        },
      },
    );
  };

  // O diálogo fica dentro do formulário da Bancada: Enter no nome salvaria a Bancada (Executar).
  const onNameKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="flex flex-col gap-2">
      <Button
        type="button"
        variant="secondary"
        fullWidth
        onClick={() => {
          setMessage(draft.ok ? null : T.invalidParameters);
          setOpen(draft.ok);
        }}
      >
        {T.save}
      </Button>
      <LiveRegion message={message} />
      <ConfirmDialog
        open={open}
        title={T.saveTitle}
        confirmLabel={save.isPending ? T.saving : T.save}
        cancelLabel={T.cancel}
        confirmBlocked={save.isPending}
        busy={save.isPending}
        destructive={false}
        onCancel={close}
        onConfirm={submit}
      >
        <p>{T.saveBody}</p>
        <Field id={nameId} label={T.nameLabel} description={T.nameHint} error={nameError}>
          {(control) => (
            <input
              {...control}
              data-initial-focus=""
              autoComplete="off"
              value={name}
              onChange={(event) => {
                setName(event.currentTarget.value);
              }}
              onKeyDown={onNameKeyDown}
            />
          )}
        </Field>
        {save.error !== null && (
          <FailureNotice
            error={save.error}
            headingLevel={3}
            messages={{ STATE_CONFLICT: T.saveConflict }}
          />
        )}
      </ConfirmDialog>
    </div>
  );
}

function useSave() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (presetDraft: PresetDraft) => createPreset(client, presetDraft),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: PRESETS_QUERY_KEY });
    },
  });
}

function nameErrorOf(name: string): string | null {
  const trimmed = name.trim();
  if (trimmed === "") {
    return T.nameRequired;
  }
  // Code points, não grafemas: é o que o `len` do Python conta no servidor.
  const length = Array.from(trimmed.normalize("NFC")).length;
  return length > MAX_NAME_LENGTH ? T.nameTooLong : null;
}
