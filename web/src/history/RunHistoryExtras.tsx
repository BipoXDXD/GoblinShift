import { RotateCcw } from "lucide-react";
import type { ReactElement } from "react";
import { Link } from "react-router";

import { workbenchFromRunPath } from "../app/paths";
import { formatDateTime } from "../format";
import { findRecipe } from "../recipes/registry";
import type { Run, RunStatus } from "../runs/api";
import { TEXT } from "../text";

const T = TEXT.history;
/** Só as concluídas publicam resultados para baixar. */
const WITH_RESULTS: ReadonlySet<RunStatus> = new Set(["succeeded", "succeeded_with_warnings"]);

/**
 * O que o Histórico acrescenta a cada linha (2.2): a disponibilidade dos resultados e "Reusar
 * configuração", que abre a Bancada com os parâmetros da execução. O link fica acima do link da
 * linha inteira (`relative`), que cobre o cartão.
 */
export function RunHistoryExtras({
  run,
  nowMs,
}: {
  readonly run: Run;
  readonly nowMs: number;
}): ReactElement | null {
  const recipe = findRecipe(run.recipe_id);
  const availability = availabilityText(run, nowMs);
  const reusable = recipe !== null && run.parameters !== null;
  if (availability === null && !reusable) {
    return null;
  }
  return (
    <div className="flex w-full flex-wrap items-center justify-between gap-3">
      {availability !== null && <p className="text-sm text-fg-muted">{availability}</p>}
      {reusable && (
        <Link
          to={workbenchFromRunPath(recipe.id, run.id)}
          className="relative inline-flex min-h-11 items-center gap-1 text-sm font-semibold"
        >
          <RotateCcw aria-hidden="true" className="size-4" />
          {T.reuse}{" "}
          <span className="sr-only">{T.reuseOf(recipe.title, formatDateTime(run.created_at))}</span>
        </Link>
      )}
    </div>
  );
}

function availabilityText(run: Run, nowMs: number): string | null {
  if (!WITH_RESULTS.has(run.status)) {
    return null;
  }
  if (run.results_expire_at === null) {
    return T.availability.unavailable;
  }
  return Date.parse(run.results_expire_at) > nowMs
    ? T.availability.until(formatDateTime(run.results_expire_at))
    : T.availability.expired;
}
