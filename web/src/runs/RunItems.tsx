import { Ban, CircleAlert, CircleCheck, Clock, Copy, type LucideIcon } from "lucide-react";
import { useId, type ReactElement } from "react";

import { Card } from "../components/ui/Card";
import { formatCount } from "../format";
import { TEXT } from "../text";
import type { RunItem, RunSummary } from "./api";

const T = TEXT.run.items;
type CountName = keyof typeof T.counts;
type Noun = keyof typeof T.nouns;

/**
 * Desfecho de cada entrada, na ordem em que foram enviadas (1.1): rejeição com o motivo,
 * avisos, contagens e repetidos. A API não traz o nome do arquivo (só o `upload_id`, sem PII no
 * detalhe), então cada entrada é identificada pela posição, a mesma ordem da Bancada.
 */
export function RunItems({
  items,
  summary,
}: {
  readonly items: readonly RunItem[];
  readonly summary: RunSummary | null;
}): ReactElement {
  const headingId = useId();
  return (
    <Card as="section" aria-labelledby={headingId}>
      <div className="flex flex-col gap-6">
        <h2 id={headingId} className="font-display text-xl font-semibold text-fg">
          {T.heading}
        </h2>
        {summary !== null && <p className="text-fg-muted">{summaryText(summary)}</p>}
        <ol aria-label={T.listLabel} className="flex flex-col gap-3">
          {items.map((item) => (
            <ItemRow key={item.position} item={item} />
          ))}
        </ol>
      </div>
    </Card>
  );
}

function summaryText(summary: RunSummary): string {
  const counted = (noun: Noun): string => {
    const count = summary[noun];
    const [singular, plural] = T.nouns[noun];
    return `${formatCount(count)} ${count === 1 ? singular : plural}`;
  };
  return T.summary({
    inputs: counted("inputs"),
    ok: counted("ok"),
    rejected: counted("rejected"),
    duplicates: counted("duplicates"),
    outputs: counted("outputs"),
  });
}

function ItemRow({ item }: { readonly item: RunItem }): ReactElement {
  const counts = countsText(item.counts);
  return (
    <li className="flex flex-col gap-2 rounded-lg bg-surface-sunken p-4">
      <p className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="font-semibold text-fg">{T.entry(item.position)}</span>
        <ItemStatus item={item} />
      </p>
      {item.error_code !== null && (
        <p className="text-danger-fg">{T.rejections[item.error_code]}</p>
      )}
      {item.duplicate_of !== null && (
        <p className="text-fg-muted">{T.duplicateOf(item.duplicate_of)}</p>
      )}
      {item.warnings.length > 0 && (
        <ul className="flex flex-col gap-1">
          {item.warnings.map((warning) => (
            <li key={warning} className="flex items-start gap-2 text-warning-fg">
              <CircleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
              <span>{T.warnings[warning]}</span>
            </li>
          ))}
        </ul>
      )}
      {counts !== null && <p className="text-sm text-fg-muted tabular-nums">{counts}</p>}
    </li>
  );
}

const STATUS_ICONS: Readonly<Record<RunItem["status"], LucideIcon>> = {
  pending: Clock,
  ok: CircleCheck,
  rejected: Ban,
  duplicate: Copy,
};

/** Estado com ícone e texto: a cor nunca é o único sinal (WCAG 1.4.1). */
function ItemStatus({ item }: { readonly item: RunItem }): ReactElement {
  const Icon =
    item.status === "ok" && item.warnings.length > 0 ? CircleAlert : STATUS_ICONS[item.status];
  const label =
    item.status === "ok" && item.warnings.length > 0 ? T.okWithWarnings : T.statuses[item.status];
  return (
    <span className="inline-flex items-center gap-1 text-sm font-semibold text-fg-muted">
      <Icon aria-hidden="true" className="size-4" />
      {label}
    </span>
  );
}

/** Só as contagens que a interface sabe nomear; uma chave nova da API fica de fora. */
function countsText(counts: Readonly<Record<string, number>>): string | null {
  const parts = Object.entries(counts).flatMap(([name, count]) =>
    isCountName(name) ? [T.counts[name](formatCount(count))] : [],
  );
  return parts.length === 0 ? null : parts.join(" · ");
}

function isCountName(name: string): name is CountName {
  return Object.hasOwn(T.counts, name);
}
