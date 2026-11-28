import { useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleHelp, CircleX, TriangleAlert, type LucideIcon } from "lucide-react";
import type { ReactElement } from "react";

import { useApiClient } from "../api/ApiClientContext";
import { ApiError } from "../api/response";
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

/** O servidor não respondeu, ou respondeu com 5xx. */
const SERVER_ERROR_MIN = 500;

/**
 * Leitura que falhou: `down` quando a API não está de pé (rede, timeout, 5xx); `unread` quando ela
 * respondeu mas não deu para ler o estado (429, corpo fora da spec), sem afirmar que caiu.
 */
type Failure = "down" | "unread";

function failureOf(error: unknown): Failure {
  if (!(error instanceof ApiError)) {
    return "down";
  }
  const { failure } = error;
  if (failure.kind === "network" || failure.kind === "timeout") {
    return "down";
  }
  return failure.status >= SERVER_ERROR_MIN ? "down" : "unread";
}

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
 * legais: serve justamente quando o login falha. Rede, timeout ou 5xx é a API fora do ar, e aí
 * processamento e fila ficam sem leitura; 429 ou corpo fora da spec deixam as três sem leitura.
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
  const failure = query.isError ? failureOf(query.error) : undefined;
  const status = failure === undefined ? query.data : undefined;

  return (
    <article className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <FocusHeading>{T.title}</FocusHeading>
      {status === undefined && failure === undefined ? (
        <p className="text-fg-muted">{T.loading}</p>
      ) : (
        <dl className="flex flex-col divide-y divide-line-soft rounded-lg bg-surface shadow-card">
          {rowsOf(status?.processing, status?.queue, failure).map((row) => (
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
  failure: Failure | undefined,
): readonly Row[] {
  return [
    apiRow(failure),
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

function apiRow(failure: Failure | undefined): Row {
  switch (failure) {
    case undefined:
      return { label: T.rows.api, tone: "success", text: T.api.up };
    case "down":
      return { label: T.rows.api, tone: "danger", text: T.api.down };
    case "unread":
      return { label: T.rows.api, tone: "unknown", text: T.unknown };
  }
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
