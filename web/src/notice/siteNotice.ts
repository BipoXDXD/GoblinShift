import { parseNoticeFile } from "./notice";
// O build do E2E troca este arquivo pelo de e2e/fixtures (GS_NOTICE_FILE no vite.config.ts).
import noticeFile from "./notice.json";

/** O aviso publicado neste build; o `siteNotice.test.ts` garante que o arquivo é válido. */
export const SITE_NOTICE = parseNoticeFile(noticeFile);
