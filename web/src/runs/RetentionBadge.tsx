import { Clock } from "lucide-react";
import type { ReactElement } from "react";

import { formatDateTime } from "../format";
import { TEXT } from "../text";
import { remainingLabel } from "./retention";

const T = TEXT.retention;
const MINUTES_PER_HOUR = 60;

interface RetentionBadgeProps {
  /** Quando os resultados são apagados (ISO 8601). */
  readonly expiresAt: string;
  /** O relógio da tela, em ms do servidor (serverClock.ts). */
  readonly nowMs: number;
}

/**
 * O prazo dos resultados em destaque (D5): data absoluta e quanto falta. Texto estático, sem
 * `role`; o relógio é só reforço, e nas últimas horas o tom vira aviso com o mesmo ícone e texto
 * (cor nunca é o único sinal). Vencido, não mostra nada: quem chama diz que expirou.
 */
export function RetentionBadge({ expiresAt, nowMs }: RetentionBadgeProps): ReactElement | null {
  const remaining = remainingLabel(expiresAt, nowMs);
  if (remaining === null) {
    return null;
  }
  const tone = remaining.urgent ? "bg-warning-bg text-warning-fg" : "bg-info-bg text-info-fg";
  // Em minutos só abaixo de 1 h: com 60 min (arredondados), "em 1 h".
  const left =
    remaining.minutes < MINUTES_PER_HOUR
      ? T.inMinutes(remaining.minutes)
      : T.inHours(remaining.hours);
  return (
    <p className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm ${tone}`}>
      <Clock aria-hidden="true" className="size-4 shrink-0" />
      {`${T.until(formatDateTime(expiresAt))} · ${left}`}
    </p>
  );
}
