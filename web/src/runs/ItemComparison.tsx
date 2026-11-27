import type { ReactElement } from "react";

import { formatBytes, formatCount } from "../format";
import { TEXT } from "../text";
import type { RunItem } from "./api";

const T = TEXT.run.items.comparison;

type Comparison = NonNullable<RunItem["comparison"]>;

interface Line {
  readonly label: string;
  readonly before: string;
  readonly after: string;
}

/**
 * Antes e depois de uma entrada, uma linha por grandeza. A seta é só visual; o leitor de tela
 * ouve "para". O `role="group"` dá nome à lista: `aria-label` direto no `<dl>` é proibido.
 * Sem nenhuma linha (mídia sem miniatura e sem áudio), não há bloco.
 */
export function ItemComparison({
  comparison,
}: {
  readonly comparison: Comparison;
}): ReactElement | null {
  const lines = linesOf(comparison);
  if (lines.length === 0) return null;
  return (
    <div role="group" aria-label={T.label}>
      <dl className="flex flex-col gap-1 text-sm text-fg-muted tabular-nums">
        {lines.map(({ label, before, after }) => (
          <div key={label} className="flex flex-wrap gap-x-2">
            <dt className="font-semibold">{label}</dt>
            <dd>
              {before} <span aria-hidden="true">→</span> <span className="sr-only">{T.to}</span>{" "}
              {after}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function linesOf(comparison: Comparison): readonly Line[] {
  switch (comparison.kind) {
    case "image":
      return [
        line(
          T.dimensions,
          dimensions(comparison.width_before, comparison.height_before),
          dimensions(comparison.width_after, comparison.height_after),
        ),
        line(T.format, T.formats[comparison.format_before], T.formats[comparison.format_after]),
        ...(comparison.bytes_after === null
          ? []
          : [
              line(
                T.size,
                formatBytes(comparison.bytes_before),
                formatBytes(comparison.bytes_after),
              ),
            ]),
      ];
    case "media":
      return [
        ...(comparison.width_after === null || comparison.height_after === null
          ? []
          : [
              line(
                T.dimensions,
                dimensions(comparison.width_before, comparison.height_before),
                dimensions(comparison.width_after, comparison.height_after),
              ),
            ]),
        ...(comparison.audio_before === null || comparison.audio_after === null
          ? []
          : [line(T.audio, T.codecs[comparison.audio_before], T.codecs[comparison.audio_after])]),
      ];
    case "table":
      return [
        line(
          T.records,
          formatCount(comparison.records_before),
          formatCount(comparison.records_after),
        ),
      ];
    case "pdf":
      return [
        line(T.pages, formatCount(comparison.pages_before), formatCount(comparison.pages_after)),
      ];
  }
}

function line(label: string, before: string, after: string): Line {
  return { label, before, after };
}

function dimensions(width: number, height: number): string {
  return `${formatCount(width)} × ${formatCount(height)}`;
}
