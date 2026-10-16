import type { ReactElement, ReactNode } from "react";
import { Link } from "react-router";

import { runPath } from "../app/paths";
import { StatusBadge } from "../components/ui/StatusBadge";
import { formatDateTime } from "../format";
import { findRecipe } from "../recipes/registry";
import type { Run } from "../runs/api";
import { TEXT } from "../text";

const T = TEXT.history;

/**
 * Execuções em linhas, das mais novas para as mais antigas: receita, quando foi criada e o estado
 * (texto, ícone e cor). O link cobre a linha inteira (card-link) e leva à tela da execução.
 */
export function RunList({
  runs,
  renderExtras,
}: {
  readonly runs: readonly Run[];
  /** O que a tela acrescenta a cada linha, abaixo do estado (o Histórico, por exemplo). */
  readonly renderExtras?: (run: Run) => ReactNode;
}): ReactElement {
  return (
    <ul aria-label={T.listLabel} className="flex flex-col gap-3">
      {runs.map((run) => (
        <RunRow key={run.id} run={run} extras={renderExtras?.(run) ?? null} />
      ))}
    </ul>
  );
}

function RunRow({ run, extras }: { readonly run: Run; readonly extras: ReactNode }): ReactElement {
  const createdAt = formatDateTime(run.created_at);
  // Receita que saiu do registro da interface ainda aparece, pelo id que a API devolve.
  const title = findRecipe(run.recipe_id)?.title ?? run.recipe_id;
  return (
    <li className="relative flex flex-wrap items-center justify-between gap-3 rounded-lg bg-surface p-4 shadow-card transition hover:shadow-float">
      <div className="flex flex-col gap-1">
        <Link to={runPath(run.id)} className="card-link font-semibold">
          {title}
          <span className="sr-only">{T.linkDetail(createdAt)}</span>
        </Link>
        <p className="text-sm text-fg-muted">{T.createdAt(createdAt)}</p>
      </div>
      <StatusBadge status={run.status} />
      {extras}
    </li>
  );
}
