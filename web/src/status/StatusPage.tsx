import { useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleHelp, CircleX, TriangleAlert, type LucideIcon } from "lucide-react";
import type { ReactElement } from "react";

import { useApiClient } from "../api/ApiClientContext";
import { FocusHeading } from "../components/a11y/FocusHeading";
import { formatTime } from "../format";
import { TEXT } from "../text";
import {
  getServiceStatus,
  SERVICE_STATUS_QUERY_KEY,
  type ProcessingLevel,
  type QueueLevel,
} from "./api";

const T = TEXT.status;
/** Releitura com a aba visível; em segundo plano o TanStack Query não conta o intervalo. */
const REFRESH_MS = 30_000;

type Tone = "success" | "warning" | "danger" | "unknown";

// Pátina no que está bem, âmbar no aviso, brasa no perigo (ADR 032); o sem leitura é neutro.
const TONES: Record<Tone, string> = {
  success: "bg-success-bg text-success-fg",
  warning: "bg-warning-bg text-warning-fg",
  danger: "bg-danger-bg text-danger-fg",
  unknown: "bg-info-bg text-info-fg",
};

const ICONS: Record<Tone, LucideIcon> = {
  success: CircleCheck,
  warning: TriangleAlert,
  danger: CircleX,
  unknown: CircleHelp,
};

const PROCESSING_TONES: Record<ProcessingLevel, Tone> = {
  normal: "success",
  slow: "warning",
  stalled: "danger",
};

const QUEUE_TONES: Record<QueueLevel, Tone> = {
  free: "success",
  busy: "warning",
  full: "danger",
};

interface Row {
  readonly label: string;
  readonly tone: Tone;
  readonly text: string;
}

/**
 * /estado: a API, o processamento e a fila em níveis, sem contagens (D9). Pública, como os textos
 * legais: serve justamente quando o login falha. Qualquer falha da leitura (rede, 503) é a API fora
 * do ar; aí processamento e fila ficam sem leitura.
 */
export function StatusPage(): ReactElement {
  const client = useApiClient();
  const query = useQuery({
    queryKey: SERVICE_STATUS_QUERY_KEY,
    queryFn: ({ signal }) => getServiceStatus(client, signal),
    // A próxima releitura é a nova tentativa; a falha já é a informação da página.
    retry: false,
    refetchInterval: REFRESH_MS,
    refetchIntervalInBackground: false,
  });
  const down = query.isError;
  const status = down ? undefined : query.data;

  return (
    <article className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <FocusHeading>{T.title}</FocusHeading>
      {status === undefined && !down ? (
        <p className="text-fg-muted">{T.loading}</p>
      ) : (
        <dl className="flex flex-col divide-y divide-line-soft rounded-lg bg-surface shadow-card">
          {rowsOf(status?.processing, status?.queue, down).map((row) => (
            <div
              key={row.label}
              className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3"
            >
              <dt className="font-semibold text-fg">{row.label}</dt>
              <dd>
                <LevelBadge tone={row.tone} text={row.text} />
              </dd>
            </div>
          ))}
        </dl>
      )}
      <div className="flex max-w-prose flex-col gap-2 text-sm text-fg-muted">
        {status !== undefined && <p>{T.checkedAt(formatTime(status.checked_at))}</p>}
        <p>{T.sameServer}</p>
      </div>
    </article>
  );
}

function rowsOf(
  processing: ProcessingLevel | undefined,
  queue: QueueLevel | undefined,
  down: boolean,
): readonly Row[] {
  return [
    {
      label: T.rows.api,
      tone: down ? "danger" : "success",
      text: down ? T.api.down : T.api.up,
    },
    {
      label: T.rows.processing,
      tone: processing === undefined ? "unknown" : PROCESSING_TONES[processing],
      text: processing === undefined ? T.unknown : T.processing[processing],
    },
    {
      label: T.rows.queue,
      tone: queue === undefined ? "unknown" : QUEUE_TONES[queue],
      text: queue === undefined ? T.unknown : T.queue[queue],
    },
  ];
}

/** Nível em texto, com ícone e cor só de reforço (estado nunca só por cor). */
function LevelBadge({ tone, text }: { readonly tone: Tone; readonly text: string }): ReactElement {
  const Icon = ICONS[tone];
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold ${TONES[tone]}`}
    >
      <Icon aria-hidden="true" className="size-4" />
      {text}
    </span>
  );
}
