import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { useId, useState, type ReactElement } from "react";
import { Link } from "react-router";

import { useMe } from "../account/useMe";
import { useApiClient } from "../api/ApiClientContext";
import { ApiError } from "../api/response";
import { workbenchPresetPath } from "../app/paths";
import { LiveRegion } from "../components/a11y/LiveRegion";
import { EmptyState } from "../components/feedback/EmptyState";
import { FailureNotice } from "../components/feedback/FailureNotice";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { formatDateTime } from "../format";
import { findRecipe } from "../recipes/registry";
import { TEXT } from "../text";
import { deletePreset, listPresets, PRESETS_QUERY_KEY, type Preset } from "./api";

const T = TEXT.presets;

/**
 * Presets na Oficina (2.2): os da conta, cada um abrindo a Bancada pré-preenchida, e Excluir
 * terciária com confirmação em vermelho. Só com a sessão confirmada: o visitante não vê a seção
 * nem gera um 401 a cada visita.
 */
export function PresetsSection(): ReactElement | null {
  const headingId = useId();
  const client = useApiClient();
  // `?.id` vale também para o /me que devolve `null` sem sessão.
  const signedIn = useMe().data?.id !== undefined;
  const presets = useQuery({
    queryKey: PRESETS_QUERY_KEY,
    queryFn: ({ signal }) => listPresets(client, signal),
    enabled: signedIn,
  });
  const removal = useRemoval();
  if (!signedIn) {
    return null;
  }
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-4">
      <h2 id={headingId} className="text-sm font-semibold text-fg-muted">
        {T.heading}
      </h2>
      <PresetsBody presets={presets.data ?? null} error={presets.error} onRemove={removal.ask} />
      {removal.failure !== null && <FailureNotice error={removal.failure} headingLevel={3} />}
      <LiveRegion message={removal.message} />
      <ConfirmDialog
        open={removal.target !== null}
        title={T.removeTitle(removal.target?.name ?? "")}
        confirmLabel={T.removeConfirm}
        cancelLabel={T.cancel}
        confirmBlocked={removal.pending}
        onCancel={removal.cancel}
        onConfirm={removal.confirm}
      >
        <p>{T.removeBody}</p>
      </ConfirmDialog>
    </section>
  );
}

interface PresetsBodyProps {
  readonly presets: readonly Preset[] | null;
  readonly error: Error | null;
  readonly onRemove: (preset: Preset) => void;
}

function PresetsBody({ presets, error, onRemove }: PresetsBodyProps): ReactElement {
  if (presets === null) {
    return error === null ? (
      <p className="text-fg-muted">{T.loading}</p>
    ) : (
      <FailureNotice error={error} headingLevel={3} />
    );
  }
  if (presets.length === 0) {
    return (
      <EmptyState title={T.empty} headingLevel={3}>
        <p>{T.emptyHint}</p>
      </EmptyState>
    );
  }
  return (
    <ul aria-label={T.listLabel} className="grid gap-4 md:grid-cols-2">
      {presets.map((preset) => (
        <PresetItem key={preset.id} preset={preset} onRemove={onRemove} />
      ))}
    </ul>
  );
}

function PresetItem({
  preset,
  onRemove,
}: {
  readonly preset: Preset;
  readonly onRemove: (preset: Preset) => void;
}): ReactElement {
  // Receita que a interface não conhece (versão nova da API): o preset aparece, sem link.
  const recipe = findRecipe(preset.recipe_id);
  return (
    <li className="flex flex-col gap-3 rounded-lg bg-surface p-6 shadow-card">
      <div className="flex flex-col gap-1">
        <h3 className="font-semibold text-fg">{preset.name}</h3>
        <p className="text-sm text-fg-muted">
          {recipe?.title ?? preset.recipe_id} · {T.updatedAt(formatDateTime(preset.updated_at))}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {recipe !== null && (
          <Link
            to={workbenchPresetPath(recipe.id, preset.id)}
            className="inline-flex min-h-11 items-center gap-1 font-semibold"
          >
            {T.open} <span className="sr-only">{T.openOf(preset.name)}</span>
            <ChevronRight aria-hidden="true" className="size-4" />
          </Link>
        )}
        <Button
          type="button"
          variant="tertiary"
          onClick={() => {
            onRemove(preset);
          }}
        >
          {T.remove} <span className="sr-only">{T.removeOf(preset.name)}</span>
        </Button>
      </div>
    </li>
  );
}

interface Removal {
  readonly target: Preset | null;
  readonly pending: boolean;
  readonly failure: Error | null;
  readonly message: string | null;
  readonly ask: (preset: Preset) => void;
  readonly cancel: () => void;
  readonly confirm: () => void;
}

/**
 * Excluir com confirmação e `If-Match`. Revisão vencida (412: o preset mudou em outro lugar) vira
 * aviso e a lista é relida; o servidor decide, sem tirar o item da lista antes da resposta.
 */
function useRemoval(): Removal {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<Preset | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (preset: Preset) => deletePreset(client, preset),
    onSuccess: (_result, preset) => {
      setMessage(T.removed(preset.name));
    },
    onError: (error) => {
      if (isStale(error)) {
        setMessage(T.changedElsewhere);
      }
    },
    onSettled: async () => {
      setTarget(null);
      await queryClient.invalidateQueries({ queryKey: PRESETS_QUERY_KEY });
    },
  });
  const failure = mutation.error !== null && !isStale(mutation.error) ? mutation.error : null;
  return {
    target,
    pending: mutation.isPending,
    failure,
    message,
    ask: (preset) => {
      mutation.reset();
      setMessage(null);
      setTarget(preset);
    },
    cancel: () => {
      if (!mutation.isPending) {
        setTarget(null);
      }
    },
    confirm: () => {
      if (target !== null && !mutation.isPending) {
        mutation.mutate(target);
      }
    },
  };
}

function isStale(error: Error): boolean {
  return (
    error instanceof ApiError &&
    error.failure.kind === "problem" &&
    error.failure.problem.code === "PRECONDITION_FAILED"
  );
}
