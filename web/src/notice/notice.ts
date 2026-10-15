/**
 * Aviso global do site (ADR 031): incidente de segurança, encerramento do serviço ou mudança nos
 * termos. O arquivo `notice.json` é versionado no repositório e embutido no bundle; publicar um
 * aviso é um PR que o edita.
 */
import { z } from "zod";

export type NoticeKind = "incident" | "shutdown" | "terms-change";

export interface SiteNotice {
  readonly id: string;
  readonly kind: NoticeKind;
  readonly title: string;
  readonly body: string;
  /** Caminho interno, aberto pelo router; nunca outro domínio. */
  readonly link: { readonly href: string; readonly label: string } | null;
  /** Início da janela, em ms desde a época, incluído. */
  readonly fromMs: number;
  /** Fim da janela, em ms, excluído (`[from, until)`); `null` fica no ar até sair do arquivo. */
  readonly untilMs: number | null;
}

const MAX_ID = 64;
const MAX_TITLE = 120;
const MAX_BODY = 600;
const MAX_LINK_LABEL = 60;
const MAX_HREF = 200;

const NO_CONTROL_CHARS = /^\P{Cc}*$/u;
const NOTICE_ID = /^[a-z0-9][a-z0-9-]*$/;
/** Caminho da própria SPA: uma barra só no começo, sem espaço nem barra invertida. */
const INTERNAL_PATH = /^\/(?![/\\])[^\s\\]*$/;

function plainText(max: number) {
  return z.string().min(1).max(max).regex(NO_CONTROL_CHARS, "caractere de controle");
}

const isoInstant = z.iso.datetime({ offset: true }).transform((value) => Date.parse(value));

const noticeSchema = z
  .strictObject({
    id: z.string().min(1).max(MAX_ID).regex(NOTICE_ID),
    kind: z.enum(["incident", "shutdown", "terms-change"]),
    title: plainText(MAX_TITLE),
    body: plainText(MAX_BODY),
    link: z
      .strictObject({
        href: z.string().max(MAX_HREF).regex(INTERNAL_PATH, "só caminho interno"),
        label: plainText(MAX_LINK_LABEL),
      })
      .optional(),
    from: isoInstant,
    until: isoInstant.optional(),
  })
  .refine((notice) => notice.until === undefined || notice.until > notice.from, {
    message: "until precisa vir depois de from",
    path: ["until"],
  });

const noticeFileSchema = z.strictObject({ notice: noticeSchema.nullable() });

/** O conteúdo do `notice.json`: o aviso, ou `null` sem aviso. Arquivo inválido lança. */
export function parseNoticeFile(raw: unknown): SiteNotice | null {
  const parsed = noticeFileSchema.safeParse(raw);
  if (!parsed.success) {
    throw new Error(`Aviso inválido em notice.json: ${z.prettifyError(parsed.error)}`);
  }
  const notice = parsed.data.notice;
  if (notice === null) {
    return null;
  }
  return {
    id: notice.id,
    kind: notice.kind,
    title: notice.title,
    body: notice.body,
    link: notice.link ?? null,
    fromMs: notice.from,
    untilMs: notice.until ?? null,
  };
}

export function isNoticeActive(notice: SiteNotice, nowMs: number): boolean {
  return notice.fromMs <= nowMs && (notice.untilMs === null || nowMs < notice.untilMs);
}

type NoticeStorage = Pick<Storage, "getItem" | "setItem">;

/** Chave no `localStorage` com o id do último aviso fechado. */
const DISMISSED_KEY = "gs-notice-dismissed";

/**
 * O armazenamento chega por função porque o próprio acesso a `window.localStorage` lança quando o
 * navegador bloqueia dados do site.
 */
export function readDismissedNoticeId(storage: () => NoticeStorage): string | null {
  try {
    return storage().getItem(DISMISSED_KEY);
  } catch {
    // Armazenamento bloqueado: o aviso aparece de novo, que é o lado seguro.
    return null;
  }
}

export function writeDismissedNoticeId(storage: () => NoticeStorage, id: string): void {
  try {
    storage().setItem(DISMISSED_KEY, id);
  } catch {
    // Sem armazenamento, o aviso fica fechado só até a próxima carga; quem chamou já o escondeu.
  }
}
