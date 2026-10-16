import {
  Ban,
  CircleAlert,
  CircleCheck,
  CircleHelp,
  Clock,
  Copy,
  type LucideIcon,
} from "lucide-react";
import { useId, type ReactElement } from "react";

import { Card } from "../components/ui/Card";
import { formatCount } from "../format";
import { TEXT } from "../text";
import type { Coded, RunItem, RunSummary } from "./api";

const T = TEXT.run.items;
type CountName = keyof typeof T.counts;
type Noun = keyof typeof T.nouns;

/**
 * Desfecho de cada entrada, na ordem em que foram enviadas (1.1): rejeição com o motivo,
 * avisos, notas, contagens e repetidos. Cada entrada é identificada pela posição, a mesma ordem
 * da Bancada, e pelo nome do arquivo enquanto a API o guarda (30 dias). A API conta a partir de 0
 * e a tela, a partir de 1. Código que a interface ainda não conhece aparece como
 * desconhecido, com o código original.
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
  const counted = (noun: Noun): string => countedText(summary[noun], T.nouns[noun]);
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
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold text-fg">{T.entry(displayPosition(item.position))}</span>
          {item.original_name !== null && (
            <span className="font-mono text-sm break-all text-fg-muted">{item.original_name}</span>
          )}
        </span>
        <ItemStatus item={item} />
      </p>
      {item.error_code !== null && (
        <p className="text-danger-fg">
          {codeText(item.error_code, T.rejections, T.unknownRejection)}
        </p>
      )}
      {item.duplicate_of !== null && (
        <p className="text-fg-muted">{T.duplicateOf(displayPosition(item.duplicate_of))}</p>
      )}
      {item.warnings.length > 0 && (
        <ul className="flex flex-col gap-1">
          {item.warnings.map((warning, index) => (
            <li key={index} className="flex items-start gap-2 text-warning-fg">
              <CircleAlert aria-hidden="true" className="mt-1 size-4 flex-none" />
              <div className="flex flex-col gap-1">
                <span>{codeText(warning, T.warnings, T.unknownWarning)}</span>
                {warning === "CSV_FORMULA_CELLS" && <FormulaCells summary={item.formula_cells} />}
              </div>
            </li>
          ))}
        </ul>
      )}
      {item.notices.map((notice, index) => (
        <p key={index} className="text-sm text-fg-muted">
          {codeText(notice, T.notices, T.unknownNotice)}
        </p>
      ))}
      {counts !== null && <p className="text-sm text-fg-muted tabular-nums">{counts}</p>}
    </li>
  );
}

const F = T.formulaCells;
// Nome de coluna é do usuário e pode ter até 120 caracteres na API; a tela mostra o começo.
const MAX_COLUMN_NAME_CHARS = 40;

/** As colunas com células que parecem fórmula e a nota sobre o "-", sempre junto do aviso (1.3). */
function FormulaCells({ summary }: { readonly summary: RunItem["formula_cells"] }): ReactElement {
  return (
    <>
      {summary !== null && (
        <ul aria-label={F.columnsLabel} className="flex flex-col gap-1 text-sm tabular-nums">
          {summary.columns.map((column, index) => (
            <li key={index}>
              {F.column(shortName(column.name), countedText(column.cells, F.cells))}
            </li>
          ))}
        </ul>
      )}
      {summary !== null && summary.more_columns > 0 && (
        <p className="text-sm">{F.more(countedText(summary.more_columns, F.columns))}</p>
      )}
      <p className="text-sm text-fg-muted">{F.minusNote}</p>
    </>
  );
}

/** Corta por caractere, não por unidade UTF-16: um emoji nunca fica pela metade. */
function shortName(name: string): string {
  const chars = Array.from(name);
  return chars.length <= MAX_COLUMN_NAME_CHARS
    ? name
    : `${chars.slice(0, MAX_COLUMN_NAME_CHARS - 1).join("")}…`;
}

type KnownStatus = keyof typeof T.statuses;

const STATUS_ICONS: Readonly<Record<KnownStatus, LucideIcon>> = {
  pending: Clock,
  ok: CircleCheck,
  rejected: Ban,
  duplicate: Copy,
};

/** Estado com ícone e texto: a cor nunca é o único sinal (WCAG 1.4.1). */
function ItemStatus({ item }: { readonly item: RunItem }): ReactElement {
  const { Icon, label } = statusLook(item);
  return (
    <span className="inline-flex items-center gap-1 text-sm font-semibold text-fg-muted">
      <Icon aria-hidden="true" className="size-4" />
      {label}
    </span>
  );
}

function statusLook({ status, warnings }: RunItem): { Icon: LucideIcon; label: string } {
  if (typeof status !== "string") {
    return { Icon: CircleHelp, label: T.unknownStatus };
  }
  if (status === "ok" && warnings.length > 0) {
    return { Icon: CircleAlert, label: T.okWithWarnings };
  }
  return { Icon: STATUS_ICONS[status], label: T.statuses[status] };
}

/** Texto de um código da lista fechada; o desconhecido mostra o código como veio. */
function codeText<K extends string>(
  code: Coded<K>,
  texts: Readonly<Record<K, string>>,
  unknown: (raw: string) => string,
): string {
  return typeof code === "string" ? texts[code] : unknown(code.raw);
}

/** A API numera as entradas a partir de 0; a pessoa conta a partir de 1. */
function displayPosition(position: number): number {
  return position + 1;
}

/** Só as contagens que a interface sabe nomear; uma chave nova da API fica de fora. */
function countsText(counts: Readonly<Record<string, number>>): string | null {
  const parts = Object.entries(counts).flatMap(([name, count]) =>
    isCountName(name) ? [countedText(count, T.counts[name])] : [],
  );
  return parts.length === 0 ? null : parts.join(" · ");
}

/** "1 arquivo", "0 arquivos", "1.200 arquivos": singular só com exatamente 1. */
function countedText(count: number, [singular, plural]: readonly [string, string]): string {
  return `${formatCount(count)} ${count === 1 ? singular : plural}`;
}

function isCountName(name: string): name is CountName {
  return Object.hasOwn(T.counts, name);
}
