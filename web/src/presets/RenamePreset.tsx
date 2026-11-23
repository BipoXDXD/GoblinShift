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
  isNotFound,
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
 * da revisão lida e os parâmetros do próprio preset. Nome repetido (409) ou recusado (422 só no
 * nome) vira erro no campo; revisão vencida (412) vira aviso e o diálogo fecha. Preset excluído em
 * outro lugar (404) mostra a falha, e a lista é relida quando o diálogo fecha.
 *
 * O diálogo fecha antes de a lista ser relida: o nome novo pode mudar a posição do item na lista
 * ordenada, e mover o `<li>` com o `<dialog>` aberto o tira da top layer, e o `close()` deixa de
 * devolver o foco. Fechado antes, o foco já está no Renomear quando o `<li>` se move, e o React o
 * devolve ao mesmo botão depois do commit (WCAG 2.4.3).
 */
export function RenamePreset({ preset }: { readonly preset: Preset }): ReactElement {
  const nameId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(preset.name);
  const [nameError, setNameError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const rename = useRename();

  // Fecha primeiro e só então relê: a lista nova chega com o diálogo já fechado.
  const closeAndRelist = (): void => {
    flushSync(() => {
      setOpen(false);
    });
    void queryClient.invalidateQueries({ queryKey: PRESETS_QUERY_KEY });
  };

  // O erro entra no DOM antes do foco, para o leitor de tela ler o campo já com a descrição.
  const showNameError = (error: string): void => {
    flushSync(() => {
      setNameError(error);
    });
    nameInput.current?.focus();
  };

  // Fecha sempre, até com o pedido em voo (o navegador pode ter fechado o <dialog>).
  // No 404, a lista é relida só depois de a falha ser lida; o item sai com o diálogo já fechado.
  // O `open` evita reler de novo no `close` que o próprio <dialog> dispara ao fechar.
  const close = (): void => {
    setOpen(false);
    setNameError(null);
    if (open && rename.error !== null && isNotFound(rename.error)) {
      void queryClient.invalidateQueries({ queryKey: PRESETS_QUERY_KEY });
    }
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
          closeAndRelist();
        },
        onError: (renameError) => {
          if (isStaleRevision(renameError)) {
            setMessage(T.changedElsewhereRename);
            closeAndRelist();
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

/** A releitura da lista (cobre também `[...PRESETS_QUERY_KEY, id]`) fica com quem fecha o diálogo. */
function useRename() {
  const client = useApiClient();
  return useMutation({
    mutationFn: ({ preset, draft }: RenameRequest) => replacePreset(client, preset, draft),
  });
}

function isFieldOrStale(error: Error): boolean {
  return isStaleRevision(error) || isNameTaken(error) || isNameRejected(error);
}
