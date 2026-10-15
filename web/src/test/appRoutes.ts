import type { RouteObject } from "react-router";

import type { ApiClient } from "../api/client";
import { createRoutes } from "../app/routes";
import type { SiteNotice } from "../notice/notice";
import type { ContentUploader } from "../upload/xhr";
import { fakeUploader } from "./fakeUploader";

/** Relógio fixo das telas nos testes: 14/10/2026 12:00 UTC. */
const TEST_NOW = Date.parse("2026-10-14T12:00:00.000Z");

interface AppRoutesOptions {
  /** Por padrão, um que confirma cada envio na hora. */
  readonly uploader?: ContentUploader;
  /** O aviso global; por padrão, nenhum. */
  readonly notice?: SiteNotice | null;
}

/**
 * As rotas do app com dublês: o cliente da API do teste, um uploader, uma tentativa só no 429, o
 * relógio fixo e o aviso global.
 */
export function appRoutes(
  client: ApiClient,
  { uploader = fakeUploader({ autoResolve: true }).uploader, notice = null }: AppRoutesOptions = {},
): RouteObject[] {
  return createRoutes({
    uploadServices: {
      client,
      uploader,
      rateLimit: { maxAttempts: 1, fallbackDelayMs: 0, random: () => 0 },
    },
    now: () => TEST_NOW,
    notice,
  });
}
