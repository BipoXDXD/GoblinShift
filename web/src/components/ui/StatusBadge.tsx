import {
  Ban,
  CircleCheck,
  CircleStop,
  CircleX,
  Clock,
  LoaderCircle,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import type { ReactElement } from "react";

import type { RunStatus } from "../../runs/api";
import { TEXT } from "../../text";

type Tone = "success" | "warning" | "danger" | "info";

const TONES: Record<Tone, string> = {
  success: "bg-success-bg text-success-fg",
  warning: "bg-warning-bg text-warning-fg",
  danger: "bg-danger-bg text-danger-fg",
  info: "bg-info-bg text-info-fg",
};

// Record sobre a união: um estado novo na API quebra a compilação até ganhar ícone e tom.
const LOOK: Record<RunStatus, { readonly icon: LucideIcon; readonly tone: Tone }> = {
  queued: { icon: Clock, tone: "info" },
  running: { icon: LoaderCircle, tone: "info" },
  stopping: { icon: CircleStop, tone: "info" },
  succeeded: { icon: CircleCheck, tone: "success" },
  succeeded_with_warnings: { icon: TriangleAlert, tone: "warning" },
  canceled: { icon: Ban, tone: "info" },
  failed: { icon: CircleX, tone: "danger" },
};

/** Estado da execução em texto, com ícone e cor só de reforço (estado nunca só por cor). */
export function StatusBadge({ status }: { readonly status: RunStatus }): ReactElement {
  const { icon: Icon, tone } = LOOK[status];
  return (
    <span
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-semibold ${TONES[tone]}`}
    >
      <Icon aria-hidden="true" className="size-4" />
      {TEXT.run.statuses[status]}
    </span>
  );
}
