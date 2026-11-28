import { RotateCcw } from "lucide-react";
import type { ReactElement } from "react";
import { Link } from "react-router";

import { workbenchFromRunPath } from "../app/paths";
import { formatDateTime } from "../format";
import { findRecipe } from "../recipes/registry";
import type { Run, RunStatus } from "../runs/api";
import { RetentionBadge } from "../runs/RetentionBadge";
import { remainingLabel } from "../runs/retention";
import { TEXT } from "../text";

const T = TEXT.history;
/** Só as concluídas publicam resultados para baixar. */
const WITH_RESULTS: ReadonlySet<RunStatus> = new Set(["succeeded", "succeeded_with_warnings"]);

/**
 * O que o Histórico acrescenta a cada linha (2.2): o prazo dos resultados e "Reutilizar
 * parâmetros", que abre a Bancada com os parâmetros da execução. O link fica acima do link da
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
  const availability = availabilityNote(run, nowMs);
  const reusable = recipe !== null && run.parameters !== null;
  if (availability === null && !reusable) {
    return null;
  }
  return (
    <div className="flex w-full flex-wrap items-center justify-between gap-3">
      {availability}
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

/** O selo do prazo (D5, sem o Apagar agora) enquanto há resultados; senão, o motivo em texto. */
function availabilityNote(run: Run, nowMs: number): ReactElement | null {
  if (!WITH_RESULTS.has(run.status)) {
    return null;
  }
  if (run.results_expire_at === null) {
    return <p className="text-sm text-fg-muted">{T.availability.unavailable}</p>;
  }
  return remainingLabel(run.results_expire_at, nowMs) === null ? (
    <p className="text-sm text-fg-muted">{T.availability.expired}</p>
  ) : (
    <RetentionBadge expiresAt={run.results_expire_at} nowMs={nowMs} />
  );
}
