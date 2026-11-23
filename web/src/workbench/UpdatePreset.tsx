import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";

import { useApiClient } from "../api/ApiClientContext";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import {
  isNotFound,
  isStaleRevision,
  PRESETS_QUERY_KEY,
  replacePreset,
  type Preset,
  type PresetReplacement,
} from "../presets/api";
import type { RecipeDraft, RecipeId } from "../recipes/types";
import { TEXT } from "../text";

const T = TEXT.presets;

interface UpdatePresetProps {
  readonly draft: RecipeDraft<RecipeId>;
  /** O preset que abriu a Bancada, com a revisão lida. */
  readonly preset: Preset;
}

/**
 * "Atualizar preset" da Bancada aberta por um preset: troca os parâmetros dele pelos do formulário,
 * com o mesmo nome, por `PUT` com o `If-Match` da revisão. Cada sucesso guarda o preset devolvido,
 * para que a próxima atualização mande a revisão nova. Revisão vencida (412) fica no diálogo.
 */
export function UpdatePreset({ draft, preset }: UpdatePresetProps): ReactElement {
  const [current, setCurrent] = useState(preset);
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const update = useUpdate();
  // Repetir daria o mesmo erro (412 com o `If-Match` vencido, 404 com o preset excluído): bloqueado
  // até fechar e reabrir.
  const blocked =
    update.isPending ||
    (update.error !== null && (isStaleRevision(update.error) || isNotFound(update.error)));

  // Fecha sempre, até com o pedido em voo (o navegador pode ter fechado o <dialog>).
  const close = (): void => {
    setOpen(false);
    if (!update.isPending) {
      update.reset();
    }
  };

  const submit = (): void => {
    if (!draft.ok || blocked) {
      return;
    }
    update.mutate(
      { preset: current, replacement: { name: current.name, ...draft.command } },
      {
        onSuccess: (updated) => {
          setCurrent(updated);
          setMessage(T.updated(updated.name));
          setOpen(false);
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-2">
      <Button
        type="button"
        variant="secondary"
        fullWidth
        onClick={() => {
          if (!update.isPending) {
            update.reset();
          }
          setMessage(draft.ok ? null : T.invalidParameters);
          setOpen(draft.ok);
        }}
      >
        {T.update} <span className="sr-only">{T.updateOf(current.name)}</span>
      </Button>
      <LiveRegion message={message} />
      <ConfirmDialog
        open={open}
        title={T.updateTitle(current.name)}
        confirmLabel={update.isPending ? T.updating : T.update}
        cancelLabel={T.cancel}
        confirmBlocked={blocked}
        busy={update.isPending}
        destructive={false}
        onCancel={close}
        onConfirm={submit}
      >
        <p>{T.updateBody}</p>
        {update.error !== null && (
          <FailureNotice
            error={update.error}
            headingLevel={3}
            messages={{ PRECONDITION_FAILED: T.changedElsewhereUpdate }}
          />
        )}
      </ConfirmDialog>
    </div>
  );
}

interface UpdateRequest {
  readonly preset: Preset;
  readonly replacement: PresetReplacement;
}

/**
 * Relê só a lista (`exact`). A origem da Bancada aberta fica como está: o `current` já guarda o
 * preset devolvido, e relê-la seria um refetch à toa. Ela não sobrevive à Bancada (`gcTime: 0`),
 * então a próxima abertura já lê a revisão nova.
 */
function useUpdate() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ preset, replacement }: UpdateRequest) =>
      replacePreset(client, preset, replacement),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: PRESETS_QUERY_KEY, exact: true });
    },
  });
}
