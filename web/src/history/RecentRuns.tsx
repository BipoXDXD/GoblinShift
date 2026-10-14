import { useQuery } from "@tanstack/react-query";
import { useId, type ReactElement } from "react";
import { Link } from "react-router";

import { useApiClient } from "../api/ApiClientContext";
import { PATHS } from "../app/paths";
import { EmptyState } from "../components/feedback/EmptyState";
import { QueryFailure } from "../components/feedback/QueryFailure";
import { buttonClassName } from "../components/ui/Button";
import type { Run } from "../runs/api";
import { TEXT } from "../text";
import { listRuns } from "./api";
import { RunList } from "./RunList";

const T = TEXT.history;
/** Quantas a Oficina mostra; o resto fica no Histórico. Uma chamada só, sem cursor. */
const RECENT_LIMIT = 5;

/** Execuções recentes na Oficina (2.2): as cinco mais novas e o caminho para o Histórico. */
export function RecentRuns(): ReactElement {
  const headingId = useId();
  const client = useApiClient();
  const recent = useQuery({
    queryKey: ["runs", "recent"],
    queryFn: ({ signal }) => listRuns(client, { limit: RECENT_LIMIT }, signal),
  });
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-4">
      <h2 id={headingId} className="text-sm font-semibold text-fg-muted">
        {T.recentHeading}
      </h2>
      <RecentBody data={recent.data?.data ?? null} error={recent.error} />
    </section>
  );
}

interface RecentBodyProps {
  readonly data: readonly Run[] | null;
  readonly error: Error | null;
}

function RecentBody({ data, error }: RecentBodyProps): ReactElement {
  if (data === null) {
    return error === null ? (
      <p className="text-fg-muted">{T.loading}</p>
    ) : (
      <QueryFailure error={error} headingLevel={3} />
    );
  }
  if (data.length === 0) {
    return (
      <EmptyState title={T.empty} headingLevel={3}>
        <p>{T.recentEmptyHint}</p>
      </EmptyState>
    );
  }
  return (
    <>
      <RunList runs={data} />
      <Link to={PATHS.history} className={`self-start ${buttonClassName({ variant: "tertiary" })}`}>
        {T.seeAll}
      </Link>
    </>
  );
}
