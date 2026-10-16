import { FileText, CalendarClock, ShieldAlert, X, type LucideIcon } from "lucide-react";
import { useState, type ReactElement } from "react";
import { Link } from "react-router";

import { TEXT } from "../text";
import {
  isNoticeActive,
  readDismissedNoticeId,
  writeDismissedNoticeId,
  type NoticeKind,
  type SiteNotice,
} from "./notice";

const T = TEXT.notice;

interface KindLook {
  readonly icon: LucideIcon;
  /** Faixa: fundo, fio inferior e texto. */
  readonly band: string;
  /** Link e botão na cor do texto da faixa, o par de contraste medido (contrast.test.ts). */
  readonly text: string;
  /** Mudança nos termos é a única que a pessoa pode fechar; incidente e encerramento ficam. */
  readonly dismissible: boolean;
}

// Âmbar (aviso) para encerramento e mudança, brasa (perigo) para incidente (ADR 031).
const WARNING_BAND = "border-warning-accent bg-warning-bg text-warning-fg";
const DANGER_BAND = "border-danger-accent bg-danger-bg text-danger-fg";

const LOOKS: Record<NoticeKind, KindLook> = {
  incident: { icon: ShieldAlert, band: DANGER_BAND, text: "text-danger-fg", dismissible: false },
  shutdown: {
    icon: CalendarClock,
    band: WARNING_BAND,
    text: "text-warning-fg",
    dismissible: false,
  },
  "terms-change": {
    icon: FileText,
    band: WARNING_BAND,
    text: "text-warning-fg",
    dismissible: true,
  },
};

interface SiteNoticeBannerProps {
  readonly notice: SiteNotice | null;
  /** Relógio em ms, o mesmo das rotas; decide se o aviso está na janela. */
  readonly now: () => number;
  /** Onde fica o id do aviso fechado; o acesso pode lançar (ver `readDismissedNoticeId`). */
  readonly storage: () => Pick<Storage, "getItem" | "setItem">;
}

/**
 * Faixa do aviso global entre a barra e o conteúdo, em todas as telas. É uma região com nome, não
 * um alerta: não toma o foco nem interrompe o leitor de tela, e vem na ordem de leitura logo
 * depois da barra.
 */
export function SiteNoticeBanner({
  notice,
  now,
  storage,
}: SiteNoticeBannerProps): ReactElement | null {
  const [dismissedId, setDismissedId] = useState(() => readDismissedNoticeId(storage));
  if (notice === null || !isNoticeActive(notice, now())) {
    return null;
  }
  const look = LOOKS[notice.kind];
  if (look.dismissible && dismissedId === notice.id) {
    return null;
  }
  const Icon = look.icon;
  const kindLabel = T.kinds[notice.kind];

  function dismiss(id: string): void {
    writeDismissedNoticeId(storage, id);
    setDismissedId(id);
  }

  return (
    <section aria-label={T.regionLabel(kindLabel)} className={`border-b-2 ${look.band}`}>
      <div className="mx-auto flex w-full max-w-5xl items-start gap-3 px-4 py-4 md:px-8">
        <Icon aria-hidden="true" className="mt-1 size-4 flex-none" />
        <div className="flex-1">
          <p className="text-sm font-semibold">{kindLabel}</p>
          <p className="font-semibold">{notice.title}</p>
          <p className="max-w-prose">
            {notice.body}
            {notice.link === null ? null : (
              <>
                {" "}
                <Link
                  to={notice.link.href}
                  className={`font-semibold underline underline-offset-4 ${look.text}`}
                >
                  {notice.link.label}
                </Link>
              </>
            )}
          </p>
        </div>
        {look.dismissible ? (
          <button
            type="button"
            onClick={() => {
              dismiss(notice.id);
            }}
            className={`inline-flex min-h-11 flex-none cursor-pointer items-center gap-2 rounded-lg px-2 font-semibold underline underline-offset-4 hover:decoration-2 ${look.text}`}
          >
            <X aria-hidden="true" className="size-4" />
            {T.dismiss}
          </button>
        ) : null}
      </div>
    </section>
  );
}
