import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type KeyboardEvent, type ReactElement } from "react";
import { flushSync } from "react-dom";

import { useApiClient } from "../api/ApiClientContext";
import { Field } from "../components/a11y/Field";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { TEXT } from "../text";
import {
  isStaleRevision,
  PRESETS_QUERY_KEY,
  replacePreset,
  type Preset,
  type PresetReplacement,
} from "./api";
import { isNameRejected, isNameTaken, normalizePresetName, presetNameError } from "./presetName";

const T = TEXT.presets;

/**
 * "Renomear" de um preset na Oficina: diálogo com o nome atual, enviado por `PUT` com o `If-Match`
 * da revisão lida e os parâmetros do próprio preset. O item continua na lista com a mesma `key`,
 * então o foco volta ao botão quando o diálogo fecha. Nome repetido (409) ou recusado (422 só no
 * nome) vira erro no campo; revisão vencida (412) vira aviso, a lista é relida e o diálogo fecha.
 */
export function RenamePreset({ preset }: { readonly preset: Preset }): ReactElement {
  const nameId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(preset.name);
  const [nameError, setNameError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const rename = useRename();

  // O erro entra no DOM antes do foco, para o leitor de tela ler o campo já com a descrição.
  const showNameError = (error: string): void => {
    flushSync(() => {
      setNameError(error);
    });
    nameInput.current?.focus();
  };

  // Fecha sempre, até com o pedido em voo (o navegador pode ter fechado o <dialog>).
  const close = (): void => {
    setOpen(false);
    setNameError(null);
    if (!rename.isPending) {
      rename.reset();
    }
  };

  const submit = (): void => {
    if (rename.isPending) {
      return;
    }
    const finalName = normalizePresetName(name);
    const error = presetNameError(finalName);
    if (error !== null) {
      showNameError(error);
      return;
    }
    if (finalName === preset.name) {
      close();
      return;
    }
    setNameError(null);
    const { recipe_id, recipe_version, parameters } = preset;
    rename.mutate(
      { preset, draft: { name: finalName, recipe_id, recipe_version, parameters } },
      {
        onSuccess: (renamed) => {
          setMessage(T.renamed(renamed.name));
          setOpen(false);
        },
        onError: (renameError) => {
          if (isStaleRevision(renameError)) {
            setMessage(T.changedElsewhereRename);
            setOpen(false);
          } else if (isNameTaken(renameError)) {
            showNameError(T.nameTaken);
          } else if (isNameRejected(renameError)) {
            showNameError(T.nameRejected);
          }
        },
      },
    );
  };

  const onNameKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit();
    }
  };

  const failure = rename.error !== null && !isFieldOrStale(rename.error) ? rename.error : null;
  return (
    <>
      <Button
        type="button"
        variant="tertiary"
        onClick={() => {
          if (!rename.isPending) {
            rename.reset();
          }
          setMessage(null);
          setNameError(null);
          setName(preset.name);
          setOpen(true);
        }}
      >
        {T.rename} <span className="sr-only">{T.renameOf(preset.name)}</span>
      </Button>
      <LiveRegion message={message} />
      <ConfirmDialog
        open={open}
        title={T.renameTitle(preset.name)}
        confirmLabel={rename.isPending ? T.renaming : T.renameConfirm}
        cancelLabel={T.cancel}
        confirmBlocked={rename.isPending}
        busy={rename.isPending}
        destructive={false}
        onCancel={close}
        onConfirm={submit}
      >
        <Field id={nameId} label={T.renameLabel} description={T.nameHint} error={nameError}>
          {(control) => (
            <input
              {...control}
              ref={nameInput}
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
        {failure !== null && <FailureNotice error={failure} headingLevel={3} />}
      </ConfirmDialog>
    </>
  );
}

interface RenameRequest {
  readonly preset: Preset;
  readonly draft: PresetReplacement;
}

/** Sucesso e revisão vencida releem a lista (cobre também `[...PRESETS_QUERY_KEY, id]`). */
function useRename() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ preset, draft }: RenameRequest) => replacePreset(client, preset, draft),
    onSettled: async (_result, error) => {
      if (error === null || isStaleRevision(error)) {
        await queryClient.invalidateQueries({ queryKey: PRESETS_QUERY_KEY });
      }
    },
  });
}

function isFieldOrStale(error: Error): boolean {
  return isStaleRevision(error) || isNameTaken(error) || isNameRejected(error);
}
