import { matchRoutes, type RouteObject } from "react-router";

import type { ApiClient } from "../api/client";
import { createRoutes } from "../app/routes";
import type { SiteNotice } from "../notice/notice";
import type { ContentUploader } from "../upload/xhr";
import { testNow } from "./clock";
import { fakeApi } from "./fakeApi";
import { fakeUploader } from "./fakeUploader";

interface AppRoutesOptions {
  /** Por padrão, um que confirma cada envio na hora. */
  readonly uploader?: ContentUploader;
  /** O aviso global; por padrão, nenhum. */
  readonly notice?: SiteNotice | null;
  /** Quem baixa as amostras da Bancada; por padrão, devolve um corpo curto para qualquer URL. */
  readonly fetchSample?: typeof fetch;
}

const sampleFromMemory: typeof fetch = () => Promise.resolve(new Response("exemplo\n"));

/**
 * As rotas do app com dublês: o cliente da API do teste, um uploader, uma tentativa só no 429, as
 * amostras da Bancada, o relógio fixo e o aviso global.
 */
export function appRoutes(
  client: ApiClient,
  {
    uploader = fakeUploader({ autoResolve: true }).uploader,
    notice = null,
    fetchSample = sampleFromMemory,
  }: AppRoutesOptions = {},
): RouteObject[] {
  return createRoutes({
    uploadServices: {
      client,
      uploader,
      rateLimit: { maxAttempts: 1, fallbackDelayMs: 0, random: () => 0 },
      fetchSample,
    },
    now: testNow,
    notice,
  });
}

/**
 * Carrega antes os módulos das telas lazy destes caminhos, chamando o `lazy` das próprias rotas.
 * Use com `await` no topo do arquivo de teste: a coleta do arquivo não tem o testTimeout, e o
 * primeiro teste deixa de pagar a transformação a frio do módulo da tela, que sob carga passa de
 * 5 s. O router continua passando pelo `lazy` em cada teste; a promessa só resolve do cache.
 * Fica fora do `setupFiles` para cada arquivo carregar só as telas que abre.
 */
export async function preloadRoutes(...paths: readonly string[]): Promise<void> {
  const routes = appRoutes(fakeApi({}).client);
  await Promise.all(paths.map((path) => loadScreen(routes, path)));
}

async function loadScreen(routes: RouteObject[], path: string): Promise<void> {
  // A tela é a folha do casamento; a moldura e a fronteira de falha acima dela não são lazy.
  const lazy = matchRoutes(routes, path)?.at(-1)?.route.lazy;
  if (typeof lazy !== "function") {
    throw new Error(`Nenhuma tela lazy responde por ${path}.`);
  }
  await lazy();
}
