// O que o service worker faz com o `POST /compartilhar` (ADR 064), sem `self`: o `sw.ts` passa a
// origem, o `CacheStorage` e o relógio, e o teste passa os falsos.

import { storeShared } from "./inbox";

const SHARE_PATH = "/compartilhar";
/** Sem como guardar os arquivos (cota, teto, corpo ilegível), a Oficina explica o motivo. */
const SHARE_UNAVAILABLE = "/?compartilhamento=indisponivel";

/** Só o `POST /compartilhar` da própria origem; o resto segue para a rede sem service worker. */
export function isShareRequest(
  request: { readonly method: string; readonly url: string },
  origin: string,
): boolean {
  const url = new URL(request.url);
  return request.method === "POST" && url.origin === origin && url.pathname === SHARE_PATH;
}

interface ReceiveContext {
  readonly caches: CacheStorage;
  readonly now: number;
  readonly newId: () => string;
  readonly origin: string;
}

/** Guarda os arquivos e devolve para onde redirecionar (303: a página abre com GET). */
export async function receiveShare(
  request: { readonly formData: () => Promise<FormData> },
  { caches, now, newId, origin }: ReceiveContext,
): Promise<string> {
  try {
    const id = await storeShared(caches, await request.formData(), { now, newId });
    return new URL(`${SHARE_PATH}?${new URLSearchParams({ id }).toString()}`, origin).href;
  } catch {
    return new URL(SHARE_UNAVAILABLE, origin).href;
  }
}
