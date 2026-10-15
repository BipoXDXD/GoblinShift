import { useId, type ReactElement } from "react";
import { Link } from "react-router";

import { runPath } from "../app/paths";
import { Card } from "../components/ui/Card";
import { formatBytes, formatDateTime } from "../format";
import { TEXT } from "../text";
import type { Me } from "./api";

const T = TEXT.account.usage;

/**
 * Uso e cotas de GET /me (1.7): o consumo em texto, com unidade, e um `meter` que só reforça o
 * número. "No limite" vem em texto, nunca só na cor da barra.
 */
export function UsageSection({ me }: { readonly me: Me }): ReactElement {
  const headingId = useId();
  const limitsHeadingId = useId();
  const { limits, usage } = me;
  return (
    <Card as="section" aria-labelledby={headingId}>
      <div className="flex flex-col gap-6">
        <h2 id={headingId} className="text-lg font-semibold text-fg">
          {T.heading}
        </h2>
        <ul className="flex flex-col gap-6">
          <UsageMeter
            label={T.runs}
            used={usage.runs_last_24h}
            limit={limits.runs_per_24h}
            format={String}
          />
          <UsageMeter
            label={T.inputBytes}
            used={usage.input_bytes_last_24h}
            limit={limits.input_bytes_per_24h}
            format={formatBytes}
          />
          <UsageMeter
            label={T.storage}
            used={usage.storage_reserved_bytes}
            limit={limits.storage_bytes}
            format={formatBytes}
          />
        </ul>
        {usage.daily_quota_resets_at !== null && (
          <p className="text-fg-muted">{T.resetsAt(formatDateTime(usage.daily_quota_resets_at))}</p>
        )}
        {usage.active_run_id !== null && (
          <p>
            <Link
              to={runPath(usage.active_run_id)}
              className="inline-flex min-h-11 items-center font-semibold"
            >
              {T.activeRun}
            </Link>
          </p>
        )}
        <section aria-labelledby={limitsHeadingId} className="flex flex-col gap-3">
          <h3 id={limitsHeadingId} className="text-sm font-semibold text-fg-muted">
            {T.limitsHeading}
          </h3>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
            <Limit term={T.fileBytes} value={formatBytes(limits.file_bytes)} />
            <Limit term={T.batchFiles} value={String(limits.batch_files)} />
            <Limit term={T.batchBytes} value={formatBytes(limits.batch_bytes)} />
            <Limit term={T.concurrentUploads} value={String(limits.concurrent_uploads)} />
          </dl>
        </section>
      </div>
    </Card>
  );
}

interface UsageMeterProps {
  readonly label: string;
  readonly used: number;
  readonly limit: number;
  readonly format: (value: number) => string;
}

function UsageMeter({ label, used, limit, format }: UsageMeterProps): ReactElement {
  const labelId = useId();
  const atLimit = used >= limit;
  return (
    <li className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <span id={labelId} className="font-semibold">
          {label}
        </span>
        <span className="tabular-nums">
          {T.ofLimit(format(used), format(limit))}
          {atLimit && <strong className="ml-2 text-danger-fg">{T.atLimit}</strong>}
        </span>
      </div>
      <meter
        aria-labelledby={labelId}
        aria-valuetext={T.ofLimit(format(used), format(limit))}
        min={0}
        max={limit}
        value={Math.min(used, limit)}
      />
    </li>
  );
}

function Limit({ term, value }: { readonly term: string; readonly value: string }): ReactElement {
  return (
    <div className="flex flex-col">
      <dt className="text-sm text-fg-muted">{term}</dt>
      <dd className="font-semibold tabular-nums">{value}</dd>
    </div>
  );
}
