import type { RouteObject } from "react-router";

import type { ApiClient } from "../api/client";
import { createRoutes } from "../app/routes";
import type { ContentUploader } from "../upload/xhr";
import { fakeUploader } from "./fakeUploader";

/** Relógio fixo das telas nos testes: 14/10/2026 12:00 UTC. */
const TEST_NOW = Date.parse("2026-10-14T12:00:00.000Z");

/**
 * As rotas do app com dublês: o cliente da API do teste, um uploader (por padrão, um que confirma
 * cada envio na hora), uma tentativa só no 429 e o relógio fixo.
 */
export function appRoutes(
  client: ApiClient,
  uploader: ContentUploader = fakeUploader({ autoResolve: true }).uploader,
): RouteObject[] {
  return createRoutes({
    uploadServices: {
      client,
      uploader,
      rateLimit: { maxAttempts: 1, fallbackDelayMs: 0, random: () => 0 },
    },
    now: () => TEST_NOW,
  });
}
